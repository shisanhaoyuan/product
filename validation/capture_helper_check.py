"""Isolated browser acceptance; simulated CAPTCHA, no real Amazon interaction."""
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
from PIL import Image
import io

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

with tempfile.TemporaryDirectory(prefix='capture-helper-test-') as tmp:
    temp = Path(tmp)
    os.environ['CATALOG_DB'] = str(temp / 'test.db')
    os.environ['CATALOG_AUTH_ENABLED'] = '0'
    from catalog.app import create_app
    from catalog.models import ImportBundle
    from catalog.images import HumanVerificationRequired
    import catalog.images
    import catalog.source_capture
    from test_prelisting import product
    app = create_app(temp / 'test.db')
    app.state.catalog.ingest(ImportBundle(namespace='test', label='browser fixture', listings=[product()]))
    before = app.state.catalog.export()
    def challenge(asin):
        raise HumanVerificationRequired('simulated verification')
    catalog.images.fetch_amazon_images = challenge
    catalog.source_capture.fetch_amazon_page = challenge
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    helper = temp / 'helper'
    shutil.copytree(ROOT / 'browser-helper', helper)
    manifest = json.loads((helper / 'manifest.json').read_text())
    # In this disposable browser only, pre-grant the same origin access the user grants in the popup.
    manifest['host_permissions'].append(origin + '/*')
    manifest['content_scripts'].append({'matches': [origin + '/*'], 'js': ['bridge.js']})
    (helper / 'manifest.json').write_text(json.dumps(manifest))
    worker = (helper / 'worker.js').read_text()
    # Extension-created initial navigations bypass Playwright routing in this Chromium build.
    # Create a blank tab in the fixture; navigate it through Playwright's mocked route below.
    (helper / 'worker.js').write_text(worker.replace(
        'url: `https://www.amazon.com/dp/${data.asin}?th=1&psc=1`', "url: 'about:blank'"))
    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(str(temp / 'profile'), channel='chromium', headless=True,
                args=[f'--disable-extensions-except={helper}', f'--load-extension={helper}'],
                viewport={'width': 1400, 'height': 1000})
            html = '''<!doctype html><meta charset="utf-8"><title>Simulated verification</title>
              <button id="manual">模拟人工验证完成</button><div id="gallery"></div>
              <script>document.querySelector('#manual').onclick=()=>{
                document.querySelector('#gallery').innerHTML='<input id="ASIN" value="B000000000"><img id="landingImage" data-old-hires="https://m.media-amazon.com/images/I/verified.jpg">';
                document.querySelector('#manual').remove();
              }</script>'''
            context.route('https://www.amazon.com/**', lambda route: route.fulfill(body=html, content_type='text/html'))
            image = io.BytesIO()
            Image.new('RGB', (160, 160), '#7997ba').save(image, format='PNG')
            context.route('https://m.media-amazon.com/**', lambda route: route.fulfill(body=image.getvalue(), content_type='image/png'))
            page = context.pages[0]
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin)
            page.locator('.product-card').first.click()
            page.get_by_role('button', name='管理图片', exact=True).first.click()
            button = page.get_by_role('button', name='按 ASIN 重新抓取')
            with context.expect_page() as opened:
                button.click()
            amazon = opened.value
            amazon.goto('https://www.amazon.com/dp/B000000000?th=1&psc=1')
            amazon.get_by_role('button', name='模拟人工验证完成').wait_for()
            expect(page.get_by_role('button', name='取消等待')).to_be_visible()
            assert page.locator('.image-capture-grid').count() == 0
            # This is a fixture button, never a real CAPTCHA.
            amazon.get_by_role('button', name='模拟人工验证完成').click()
            expect(page.locator('.image-capture-grid')).to_be_visible(timeout=15000)
            expect(page.get_by_text('已核验 ASIN B000000000，抓取到 1 张图片；请选择需要添加的图片。')).to_be_visible()
            expect(page.locator('.image-capture-grid img')).to_have_js_property('naturalWidth', 160)
            assert app.state.catalog.export() == before
            page.screenshot(path=str(ROOT / 'validation/capture-helper-success.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(ROOT / 'validation/capture-helper-mobile.png'))
            assert page.locator('.image-manager-dialog').evaluate('(el) => el.getBoundingClientRect().width <= innerWidth')
            page.set_viewport_size({'width': 1400, 'height': 1000})
            with context.expect_page() as opened:
                button.click()
            opened.value.close()
            expect(page.get_by_role('alert')).to_contain_text('关闭', timeout=10000)
            with context.expect_page() as opened:
                button.click()
            page.get_by_role('button', name='取消等待').click()
            expect(page.get_by_role('alert')).to_contain_text('取消')
            assert not errors, errors
            assert app.state.catalog.export() == before
            page.once('dialog', lambda dialog: dialog.accept())
            page.get_by_role('button', name='关闭图片查看').click()
            # Exercise source mode with the real extension; every Amazon page is mocked.
            context.unroute('https://www.amazon.com/**')
            source_html = '''<!doctype html><meta charset="utf-8"><title>Source verification fixture</title>
              <button id="manual">模拟人工验证完成</button><div id="product"></div>
              <script>document.querySelector('#manual').onclick=()=>{
                document.querySelector('#product').innerHTML=`
                  <input id="ASIN" value="B000000000"><span id="glow-ingress-line2">New York 10001</span>
                  <div id="desktop_buybox"><div id="corePrice_feature_div">
                    <span class="a-offscreen">$37.99</span></div>
                    <a id="sellerProfileTriggerId">Main Seller</a>
                    <div id="deliveryBlockMessage">FREE delivery Friday, November 6</div></div>
                  <a id="aod-ingress-link" href="#" onclick="document.querySelector('#offers').hidden=false;return false">Other sellers on Amazon</a>
                  <div id="offers" hidden><div id="aod-offer-list"><div id="aod-offer">
                    <div id="aod-offer-heading">New</div><div id="aod-offer-price">
                    <span class="a-offscreen">$54.00</span>
                    <span class="a-text-price"><span class="a-offscreen">$60.00</span></span></div>
                    <div id="aod-offer-soldBy">Sold by <a>Actual Seller</a></div>
                    <div id="aod-offer-delivery">$7.99 delivery Monday, November 9</div>
                  </div></div></div>`;
                document.querySelector('#manual').remove();
              }</script>'''
            context.route('https://www.amazon.com/**', lambda route: route.fulfill(body=source_html, content_type='text/html'))
            parent_panel = page.locator('.source-capture').first
            with context.expect_page() as opened:
                parent_panel.get_by_role('button', name='采集卖家与报价').click()
            amazon = opened.value
            amazon.goto('https://www.amazon.com/dp/B000000000?th=1&psc=1')
            expect(parent_panel.get_by_role('status')).to_contain_text('等待来源页面')
            amazon.get_by_role('button', name='模拟人工验证完成').click()
            expect(parent_panel.locator('dd').first).to_have_text('Actual Seller', timeout=15000)
            expect(parent_panel.locator('dd').nth(1)).to_have_text('$54.00')
            expect(parent_panel.locator('dd').nth(2)).to_have_text('$7.99')
            expect(parent_panel.locator('dd').nth(3)).to_contain_text('November 9')
            expect(parent_panel.locator('dd').nth(4)).to_have_text('$60.00')
            page.reload()
            expect(parent_panel.locator('dd').first).to_have_text('Actual Seller')
            parent_panel.scroll_into_view_if_needed()
            page.screenshot(path=str(ROOT / 'validation/source-capture-desktop.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            parent_panel.scroll_into_view_if_needed()
            assert parent_panel.evaluate('(el) => el.getBoundingClientRect().width <= innerWidth')
            page.screenshot(path=str(ROOT / 'validation/source-capture-mobile.png'))
            page.set_viewport_size({'width': 1400, 'height': 1000})
            child_panel = page.locator('#variant-detail .source-capture')
            expect(child_panel).to_contain_text('暂无来源采集记录')
            with context.expect_page() as opened:
                child_panel.get_by_role('button', name='采集卖家与报价').click()
            amazon = opened.value
            amazon.goto('https://www.amazon.com/dp/B000000000?th=1&psc=1')
            amazon.get_by_role('button', name='模拟人工验证完成').click()
            expect(child_panel.locator('dd').first).to_have_text('Actual Seller', timeout=15000)
            assert app.state.catalog.export() == before
            with app.state.catalog.connect() as db:
                rows = db.execute('SELECT variant_sku,asin FROM source_captures').fetchall()
                assert {(r[0], r[1]) for r in rows} == {('', 'B000000000'), ('child-0', 'B000000000')}
            assert not errors, errors
            context.close()
            # Missing helper must offer installation, without pretending the challenge was solved.
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(origin)
            page.locator('.product-card').first.click()
            page.get_by_role('button', name='管理图片', exact=True).first.click()
            page.get_by_role('button', name='按 ASIN 重新抓取').click()
            expect(page.get_by_role('alert')).to_contain_text('安装', timeout=10000)
            expect(page.get_by_role('link', name='下载验证助手')).to_be_visible()
            assert page.request.get(origin + '/api/products/images/helper.zip').status == 200
            browser.close()
            print('Browser flow passed: image/source handoff, simulated manual completion, lower Sellers list, persisted readback, parent/child isolation, desktop/mobile, close, cancel, missing helper; product data unchanged.')
    finally:
        server.should_exit = True
        thread.join(timeout=10)
