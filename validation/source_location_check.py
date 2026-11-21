"""Mocked Amazon pages: address handoff, persistent browser state, cached library covers."""
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

from PIL import Image
from playwright.sync_api import sync_playwright, expect
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

with tempfile.TemporaryDirectory(prefix='jiwu-location-test-') as folder:
    os.environ['CATALOG_AUTH_ENABLED'] = '0'
    from catalog.app import create_app
    from catalog.models import ImportBundle
    from test_prelisting import product
    import catalog.listing_vision
    import catalog.source_capture
    from catalog.images import HumanVerificationRequired
    raw = io.BytesIO()
    Image.new('RGB', (300, 300), '#7a9bb8').save(raw, format='PNG')
    catalog.listing_vision.fetch_image = lambda _: raw.getvalue()
    def blocked(_):
        raise HumanVerificationRequired('fixture')
    catalog.source_capture.fetch_amazon_page = blocked
    app = create_app(Path(folder) / 'catalog.db')
    app.state.catalog.ingest(ImportBundle(namespace='test', label='fixture', listings=[
        product(9, images=['https://pg-cdn-a2.datacaciques.com/photo.jpg'])]))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context(viewport={'width': 1400, 'height': 1000})
            # External image loading deliberately fails; only our bound server cache works.
            context.route('https://pg-cdn-a2.datacaciques.com/**', lambda r: r.abort())
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(origin)
            cover = page.locator('.product-card img')
            expect(cover).to_have_js_property('naturalWidth', 300)
            assert '/images/cached?' in cover.get_attribute('src')
            page.locator('.product-card').click()
            expect(page.get_by_role('button', name='打开浏览器设置地区并采集').first).to_be_visible()
            expect(page.get_by_role('img', name='主商品主图')).to_have_js_property('naturalWidth', 300)
            for width in (1400, 390):
                page.set_viewport_size({'width': width, 'height': 1000})
                panel = page.locator('.source-capture').first
                panel.scroll_into_view_if_needed()
                assert panel.evaluate('(el) => el.getBoundingClientRect().right <= innerWidth + 1')
                page.screenshot(path=str(ROOT / f'validation/source-location-{width}.png'))
            assert not errors, errors

            # Run the actual content script with a stub extension bridge, against mocked HTML.
            amazon = context.new_page()
            amazon.add_init_script('''window.submissions=[];
              window.chrome={runtime:{sendMessage:async data=>{
                if(data.action==='task') return {asin:location.pathname.split('/').pop(),mode:'source'};
                if(data.action==='page') {window.submissions.push(data);return {accepted:true};}
              }}};''')
            fixture = '''<input id="ASIN"><span id="glow-ingress-line2"></span>
              <div id="desktop_buybox"><span class="a-offscreen">$39.00</span></div>
              <button id="set">Set 10001</button><script>
                document.querySelector('#ASIN').value=location.pathname.split('/').pop();
                document.querySelector('#glow-ingress-line2').textContent=localStorage.zip||'90001';
                document.querySelector('#set').onclick=()=>{localStorage.zip='10001';location.reload();};
              </script>'''
            context.route('https://www.amazon.com/**', lambda r:r.fulfill(body=fixture, content_type='text/html'))
            amazon.goto('https://www.amazon.com/dp/B000000009')
            amazon.add_script_tag(path=str(ROOT / 'browser-helper/amazon.js'))
            expect(amazon.locator('#jiwu-location-help')).to_contain_text('10001', timeout=5000)
            assert amazon.evaluate('submissions.length') == 0
            amazon.get_by_role('button', name='Set 10001').click()
            amazon.wait_for_load_state()
            amazon.add_script_tag(path=str(ROOT / 'browser-helper/amazon.js'))
            amazon.wait_for_function('submissions.length===1', timeout=12000)
            assert '10001' in amazon.evaluate('submissions[0].html')
            # Next product in the same profile reuses the address without another prompt.
            amazon.goto('https://www.amazon.com/dp/B000000010')
            amazon.add_script_tag(path=str(ROOT / 'browser-helper/amazon.js'))
            amazon.wait_for_function('submissions.length===1', timeout=12000)
            assert amazon.evaluate('submissions[0].asin') == 'B000000010'
            assert amazon.locator('#jiwu-location-help').count() == 0
            browser.close()
            print('PASS: cached covers, responsive source UI, ZIP handoff, persistent ZIP for next ASIN')
    finally:
        server.should_exit = True
