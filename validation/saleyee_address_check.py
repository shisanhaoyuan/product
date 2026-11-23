"""Product address multiselect and detail regression with disposable API fixtures."""
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from catalog.app import create_app
from catalog.saleyee import SaleyeeSettings, SyncInput, sync_page
from test_saleyee_addresses import address_fixture


def main():
    with tempfile.TemporaryDirectory(prefix='jiwu-saleyee-address-ui-') as directory, pytest.MonkeyPatch.context() as patch:
        patch.setenv('CATALOG_AUTH_ENABLED', '0')
        patch.setenv('CATALOG_LISTING_AI_ENABLED', '0')
        patch.setenv('CATALOG_SALEYEE_ACCOUNT', 'fixture-only')
        patch.setenv('CATALOG_SALEYEE_ACCESS_TOKEN', '')
        app = create_app(Path(directory) / 'catalog.db')
        catalog = app.state.catalog
        sync_page(catalog, SaleyeeSettings(), SyncInput(), client=address_fixture())
        pid = next(p['id'] for p in catalog.find(source='Saleyee')['items'] if p['sku'] == 'S1')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            assert server.started
            origin = f'http://127.0.0.1:{port}'
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='chrome', headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors, writes = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: writes.append(request.url) if request.method == 'POST' else None)
                page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
                page.goto(origin + '/#/saleyee-products')
                count = page.locator('.heading-count')
                expect(count).to_have_text('2')
                page.locator('summary[aria-label^="发货地址："]').click()
                shipping = page.get_by_role('group', name='发货地址', exact=True)
                search = page.get_by_role('searchbox', name='搜索发货地址')
                search.fill('30297')
                expect(shipping.get_by_role('button')).to_have_count(1)
                east = shipping.get_by_role('button', name='US GA FOREST PARK', exact=False)
                east.click()
                expect(count).to_have_text('1')
                expect(east).to_have_attribute('aria-pressed', 'true')
                search.fill('91762')
                west = shipping.get_by_role('button', name='US CA Ontario', exact=False)
                west.click()
                expect(count).to_have_text('2')
                expect(west).to_have_attribute('aria-pressed', 'true')
                search.fill('30297')
                east.click()
                expect(east).to_have_attribute('aria-pressed', 'false')
                page.locator('summary[aria-label^="退货地址："]').click()
                returns = page.get_by_role('group', name='退货地址', exact=True)
                returns.get_by_role('button', name='US NJ Fieldsboro', exact=False).click()
                expect(count).to_have_text('1')
                page.reload()
                expect(count).to_have_text('1')
                expect(page.locator('summary[aria-label^="发货地址："]')).to_contain_text('91762')
                expect(page.locator('summary[aria-label^="退货地址："]')).to_contain_text('08505')
                output = Path('/tmp/jiwu-saleyee-address')
                output.mkdir(parents=True, exist_ok=True)
                page.locator('summary[aria-label^="发货地址："]').click()
                page.screenshot(path=str(output / 'filters.png'))
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(output / 'mobile-filters.png'))
                page.goto(origin + '/#/saleyee-product/' + pid)
                section = page.locator('.table-scroll').filter(has=page.get_by_role('heading', name='全部发货仓库与地址'))
                expect(section).to_contain_text('USS-1')
                expect(section).to_contain_text('USW-1')
                expect(section).to_contain_text('30297')
                expect(section).to_contain_text('91762')
                expect(section.locator('tbody tr').first.locator('td').last).to_have_text('33')
                expect(section.locator('tbody tr').nth(1).locator('td').last).to_have_text('19')
                returns_table = page.locator('.table-scroll').filter(has=page.get_by_role('heading', name='退货地址', exact=True))
                expect(returns_table).to_contain_text('08505')
                section.scroll_into_view_if_needed()
                page.screenshot(path=str(output / 'mobile-addresses.png'))
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors, errors
                assert not writes, writes
                browser.close()
            print('Passed: searchable address multiselect/toggle, AND filters, reload, both warehouses/stock, return address, mobile overflow, no writes.')
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == '__main__':
    main()
