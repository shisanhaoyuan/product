"""Real editor regression using disposable fixtures and mocked Walmart transport."""
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

from test_listing_variants import variant_publishing
from test_walmart_accounts import vault
from test_walmart_publishing import PT, ready
from catalog.walmart_listing_schema import sections


def main():
    with tempfile.TemporaryDirectory(prefix='jiwu-variant-editor-') as directory, pytest.MonkeyPatch.context() as patch:
        for key in ('CATALOG_AUTH_ENABLED', 'CATALOG_LISTING_AI_ENABLED', 'CATALOG_LISTING_RESULT_WORKER'):
            patch.setenv(key, '0')
        key = vault.__wrapped__(Path(directory), patch)
        fixture = variant_publishing.__wrapped__(Path(directory), key)
        client, catalog, _, _, batch, base, remote = fixture
        shape = sections(remote.schema, PT)['visible']
        shape['properties'].update(finish={'type': 'string'}, size={'type': 'string'})
        shape['required'].append('color')
        doc = ready(fixture)
        first, second = doc['rows']
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(client.app, host='127.0.0.1', port=port, log_level='error'))
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
                page = browser.new_page(viewport={'width': 1600, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                # Fixture images are placeholders; never access external websites.
                page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
                page.goto(origin + '/#/prelisting/' + batch['id'])
                table = page.locator('.variant-group')
                expect(table.locator('table th')).to_have_count(3)
                expect(table.locator('thead')).to_contain_text('颜色')
                expect(table.locator('thead')).not_to_contain_text('饰面')
                expect(table.locator('thead')).not_to_contain_text('尺寸')
                table_first = table.locator(f'[data-listing-path="variants.{first["platform_sku"]}.visible.color"] input')
                table_second = table.locator(f'[data-listing-path="variants.{second["platform_sku"]}.visible.color"] input')
                detail = page.locator('#listing-required [data-listing-path="visible.color"] input')
                expect(detail).to_be_enabled()
                detail.fill('Child editor Brown')
                expect(table_first).to_have_value('Child editor Brown')
                table_first.fill('Table Coffee')
                expect(detail).to_have_value('Table Coffee')
                expect(table_second).to_have_value('Blue')
                # Saving uses the real local API and must retain this exact SKU value.
                expect(page.locator('.listing-save-state')).to_have_text('资料已保存')
                page.reload()
                expect(table_first).to_have_value('Table Coffee')
                expect(detail).to_have_value('Table Coffee')
                page.get_by_role('button', name='填写变体 ' + second['source_sku'], exact=True).click()
                expect(detail).to_have_value('Blue')
                detail.fill('Child editor Navy')
                expect(table_second).to_have_value('Child editor Navy')
                expect(table_first).to_have_value('Table Coffee')
                table.scroll_into_view_if_needed()
                output = Path(os.environ.get('CATALOG_TEST_OUTPUT', '/tmp/jiwu-variant-editor'))
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / 'shared-color.png'))
                assert not errors, errors
                browser.close()
            print('Passed: actual color-only columns, edits in both directions, SKU isolation, save and reload.')
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            catalog.close()


if __name__ == '__main__':
    main()
