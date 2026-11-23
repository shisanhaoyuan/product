"""Browser checks for shared range controls; no remote APIs or persistent data."""
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from catalog.app import create_app
from test_product_ranges import range_fixture


def main():
    with tempfile.TemporaryDirectory(prefix='jiwu-product-range-ui-') as directory, pytest.MonkeyPatch.context() as patch:
        patch.setenv('CATALOG_AUTH_ENABLED','0')
        patch.setenv('CATALOG_LISTING_AI_ENABLED','0')
        app=create_app(Path(directory)/'catalog.db')
        app.state.catalog.ingest(range_fixture())
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        try:
            for _ in range(100):
                if server.started:break
                time.sleep(.05)
            assert server.started
            origin=f'http://127.0.0.1:{port}'
            output=Path('/tmp/jiwu-product-ranges');output.mkdir(parents=True,exist_ok=True)
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(channel='chrome',headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000})
                errors,writes=[],[]
                page.on('pageerror',lambda e: errors.append(str(e)))
                page.on('request',lambda r: writes.append(r.url) if r.method=='POST' else None)
                page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
                for name,path,total,available,stock,price in (
                    ('us','/giga-products',2,1,'33','69.33'),
                    ('canada','/giga-canada-products',1,1,'5','55'),
                    ('saleyee','/saleyee-products',2,1,'19','22.50'),
                    ('amazon','/products',3,2,'15','100.25'),
                ):
                    page.goto(origin+'/#'+path)
                    count=page.locator('.heading-count')
                    expect(count).to_have_text(str(total))
                    page.locator('summary[aria-label^="筛选库存："]').click()
                    page.get_by_role('button',name='有库存（可售库存大于 0）',exact=True).click()
                    expect(count).to_have_text(str(available))
                    range_box=page.get_by_role('group',name='库存与价格范围',exact=True)
                    range_box.get_by_role('spinbutton',name='库存下限',exact=True).fill(stock)
                    range_box.get_by_role('spinbutton',name='库存上限',exact=True).fill(stock)
                    range_box.get_by_role('spinbutton',name='价格下限',exact=True).fill(price)
                    range_box.get_by_role('spinbutton',name='价格上限',exact=True).fill(price)
                    range_box.get_by_role('spinbutton',name='价格上限',exact=True).press('Enter')
                    expect(count).to_have_text('1')
                    expect(range_box).to_contain_text('范围已应用')
                    assert 'stock_min='+stock in page.url
                    page.reload()
                    expect(count).to_have_text('1')
                    expect(range_box.get_by_role('spinbutton',name='价格下限',exact=True)).to_have_value(price)
                    # Invalid bounds never navigate or silently broaden the selection.
                    saved_url=page.url
                    range_box.get_by_role('spinbutton',name='库存下限',exact=True).fill('40')
                    range_box.get_by_role('spinbutton',name='库存上限',exact=True).fill('1')
                    expect(range_box).to_contain_text('库存下限不能大于上限')
                    expect(range_box.get_by_role('button',name='筛选范围',exact=True)).to_be_disabled()
                    range_box.get_by_role('spinbutton',name='库存上限',exact=True).press('Enter')
                    assert page.url==saved_url
                    range_box.get_by_role('button',name='清空范围',exact=True).click()
                    expect(count).to_have_text(str(available))
                    expect(range_box.get_by_role('spinbutton',name='库存下限',exact=True)).to_have_value('')
                    # One-sided bounds apply along with the inventory dropdown.
                    range_box.get_by_role('spinbutton',name='价格上限',exact=True).fill('10')
                    range_box.get_by_role('button',name='筛选范围',exact=True).click()
                    expect(count).to_have_text('0')
                    page.locator('#reset').click()
                    expect(count).to_have_text(str(total))
                    expect(range_box.get_by_role('spinbutton',name='价格上限',exact=True)).to_have_value('')
                    page.locator('#filters').screenshot(path=str(output/(name+'-filters.png')))
                    if name=='amazon':
                        page.set_viewport_size({'width':390,'height':844})
                        range_box.scroll_into_view_if_needed()
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                        page.screenshot(path=str(output/'mobile-filters.png'))
                assert not errors,errors
                assert not writes,writes
                browser.close()
            print('Passed: all four libraries, in-stock toggle, inclusive ranges, Enter/apply, invalid bounds, reload, clear/reset, mobile, no writes.')
        finally:
            server.should_exit=True;thread.join(timeout=10)


if __name__=='__main__':main()
