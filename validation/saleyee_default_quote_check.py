"""Legacy default quote regression using only disposable stored API fixtures."""
import os
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
from catalog.models import ImportBundle
from catalog.saleyee import SaleyeeSettings
from test_saleyee import legacy_default_quote


def main():
    with tempfile.TemporaryDirectory(prefix='jiwu-saleyee-quote-') as directory, pytest.MonkeyPatch.context() as patch:
        patch.setenv('CATALOG_AUTH_ENABLED', '0')
        patch.setenv('CATALOG_LISTING_AI_ENABLED', '0')
        patch.setenv('CATALOG_SALEYEE_ACCOUNT', 'fixture-only')
        patch.setenv('CATALOG_SALEYEE_ACCESS_TOKEN', '')
        app = create_app(Path(directory) / 'catalog.db')
        catalog = app.state.catalog
        settings = SaleyeeSettings()
        catalog.ingest(ImportBundle(namespace=settings.namespace, label='legacy',
                                    listings=[legacy_default_quote(settings)]))
        pid = catalog.find(source='Saleyee')['items'][0]['id']
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
                errors = []
                writes = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: writes.append(request.url) if request.method == 'POST' else None)
                page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
                page.goto(origin + '/#/saleyee-products')
                summary = page.locator('.saleyee-quick').first
                expect(summary).to_contain_text('采购价 $69.29')
                expect(summary).to_contain_text('库存 90')
                page.goto(origin + '/#/saleyee-product/' + pid)
                selector = page.locator('.saleyee-selection select')
                expect(selector).to_have_value('1')
                expect(selector.locator('option:checked')).to_contain_text('Standard Shipping')
                expect(selector.locator('option:checked')).to_contain_text('$69.29')
                expect(page.locator('.saleyee-metrics')).to_contain_text('$69.29')
                expect(page.locator('.saleyee-metrics')).to_contain_text('90')
                page.reload()
                expect(selector).to_have_value('1')
                output = Path(os.environ.get('CATALOG_TEST_OUTPUT', '/tmp/jiwu-saleyee-default'))
                output.mkdir(parents=True, exist_ok=True)
                page.locator('.saleyee-selection').scroll_into_view_if_needed()
                page.screenshot(path=str(output / 'default-quote.png'))
                assert not writes, writes
                assert not errors, errors
                browser.close()
            print('Passed: legacy list price/stock, actual default option selected, detail/reload, no refresh or external request.')
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == '__main__':
    main()
