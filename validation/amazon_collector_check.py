"""Own extension → isolated API → library. All Amazon pages are local fixtures."""
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time

from playwright.sync_api import sync_playwright, expect
import uvicorn

ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_amazon_import import html, A, B
from catalog.app import create_app
import catalog.source_images
from catalog.source_capture import amazon_source, Document

with tempfile.TemporaryDirectory(prefix='jiwu-collector-') as folder:
    temp=Path(folder)
    os.environ.update(CATALOG_AUTH_ENABLED='0',CATALOG_LISTING_AI_ENABLED='0',CATALOG_LISTING_RESULT_WORKER='0',CATALOG_AMAZON_BROWSER_PYTHON='')
    app=create_app(temp/'test.db')
    catalog.source_images.collect_amazon_images=lambda *a:dict(state='complete',images=[],saved=[],message='fixture')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    threading.Thread(target=server.run,daemon=True).start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    helper=temp/'helper';shutil.copytree(ROOT/'browser-helper',helper)
    manifest=json.loads((helper/'manifest.json').read_text())
    manifest['host_permissions'].append(origin+'/*')
    manifest['content_scripts'].append(dict(matches=[origin+'/*'],js=['bridge.js']))
    (helper/'manifest.json').write_text(json.dumps(manifest))
    worker=(helper/'worker.js').read_text().replace('url: `https://www.amazon.com/dp/${data.asin}?th=1&psc=1`',"url: 'about:blank'")
    (helper/'worker.js').write_text(worker)
    try:
        with sync_playwright() as pw:
            context=pw.chromium.launch_persistent_context(str(temp/'profile'),channel='chromium',headless=True,
                args=[f'--disable-extensions-except={helper}',f'--load-extension={helper}'],viewport=dict(width=1440,height=1000))
            def fixture(route):
                asin=B if B in route.request.url else A
                body=html(asin,price='37.99' if asin==A else '41.99')
                body+=f'<div id="variation_color_name"><span class="selection">{"Brown" if asin==A else "White"}</span><span data-asin="{B}"></span></div>'
                body+='<script type="application/json">'+json.dumps({'asinVariationValues':{A:{'color_name':0},B:{'color_name':1}}})+'</script>'
                route.fulfill(body=body,content_type='text/html')
            context.route('https://www.amazon.com/**',fixture)
            page=context.pages[0];errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(origin+'/#/integrations')
            expect(page.get_by_role('heading',name='亚马逊 · 集物自有采集')).to_be_visible()
            # Exercise actual XLSX parsing through the drop zone.
            from openpyxl import Workbook
            wb=Workbook();wb.active.append(['商品链接']);wb.active.append(['https://www.amazon.com/dp/'+A]);buf=io.BytesIO();wb.save(buf)
            data=page.evaluate_handle('bytes => {const d=new DataTransfer();d.items.add(new File([new Uint8Array(bytes)],"links.xlsx",{type:"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}));return d;}',list(buf.getvalue()))
            page.locator('.collector-drop').dispatch_event('drop',{'dataTransfer':data})
            expect(page.get_by_label('待采集 ASIN 或商品网址')).to_have_value(A)
            page.screenshot(path=str(ROOT/'validation/amazon-collector-desktop.png'))
            with context.expect_page() as opened:page.get_by_role('button',name='开始采集并入库',exact=True).click()
            amazon=opened.value
            with context.expect_page(timeout=30000) as second:
                amazon.goto('https://www.amazon.com/dp/'+A)
            second.value.goto('https://www.amazon.com/dp/'+B)
            expect(page.locator('.collector-progress')).to_contain_text('新建 1 件',timeout=30000)
            products=app.state.catalog.export()
            assert len(products)==1
            variants={v['asin']:v for v in products[0]['variants']}
            assert set(variants)=={A,B}
            assert variants[A]['price']=='37.99' and variants[B]['price']=='41.99'
            assert variants[B]['attributes']['color_name']=='White'
            assert not errors,errors
            page.set_viewport_size(dict(width=390,height=844))
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(ROOT/'validation/amazon-collector-mobile.png'),full_page=True)
            context.close()
            # Shared extractor: real maps only, dropdown-only child, no recommendation map.
            browser=pw.chromium.launch(channel='chrome',headless=True)
            tab=browser.new_page();tab.set_content(html()+f'<select id="variation_size_name"><option value="0,{B}">Large</option></select><script type="application/json">'+json.dumps({'dimensionToAsinMap':{'0':A,'1':B},'asinVariationValues':{'B999999998':{},'B999999999':{}}})+'</script>')
            tab.add_script_tag(path=str(ROOT/'browser-helper/product.js'))
            markup=tab.evaluate('jiwuProductDetails()');doc=Document(markup).root
            assert json.loads(doc.ids('jiwu-variants')[0].text())==[B,A]
            assert json.loads(doc.ids('jiwu-variant-evidence')[0].text())['complete']
            # Lazy sellers list: second quote appears only after clicking show more.
            row='<div id="aod-offer"><div id="aod-offer-heading">New</div><span class="a-price"><span class="a-offscreen">$45.00</span></span></div>'
            tab.set_content(html()+f'<div id="aod-container"><div id="aod-offer-list">{row}</div><button id="aod-show-more-offers">More</button></div>')
            tab.evaluate('row => document.querySelector("#aod-show-more-offers").onclick=()=>{document.querySelector("#aod-offer-list").insertAdjacentHTML("beforeend",row);document.querySelector("#aod-show-more-offers").remove();}',row)
            tab.add_script_tag(path=str(ROOT/'browser-helper/offers.js'))
            tab.evaluate('globalThis.read=jiwuOfferReader()')
            result=''
            for _ in range(6):
                result=tab.evaluate('read()')
                if result:break
            quote=amazon_source(f'<input id="ASIN" value="{A}">'+result,A)
            assert quote.get('price') is None and any('多条' in m for m in quote['issues'])
            browser.close()
            print('PASS: real extension, XLSX drop, child discovery, separate prices/attributes, import, mobile, lazy offers, unrelated map exclusion')
    finally:
        server.should_exit=True
