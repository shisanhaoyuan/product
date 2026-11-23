"""Real editor regression using disposable fixtures and mocked Walmart transport."""
import os
import copy
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

import pytest
import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright

from test_listing_variants import variant_publishing_fixture
from test_walmart_accounts import vault
from test_walmart_publishing import PT, ready
from catalog.walmart_listing_schema import sections
from catalog.walmart_publishing_client import WalmartPublishingClient


def main():
    with tempfile.TemporaryDirectory(prefix='jiwu-variant-editor-') as directory, pytest.MonkeyPatch.context() as patch:
        for key in ('CATALOG_AUTH_ENABLED', 'CATALOG_LISTING_AI_ENABLED', 'CATALOG_LISTING_RESULT_WORKER'):
            patch.setenv(key, '0')
        key = vault.__wrapped__(Path(directory), patch)
        fixture = variant_publishing_fixture(Path(directory), key, count=6)
        client, catalog, _, _, batch, base, remote = fixture
        shape = sections(remote.schema, PT)['visible']
        shape['properties'].update(finish={'type': 'string'}, size={'type': 'string'})
        shape['required'].append('color')
        other_type = 'End Tables'
        original_handler = remote.handler
        def handler(request):
            if request.url.path == '/v3/items/taxonomy':
                return httpx.Response(200, json={'itemTaxonomy': [{'category': 'Furniture', 'productTypeGroup': [
                    {'productTypeGroupName': 'Furniture', 'productType': [
                        {'productTypeName': PT}, {'productTypeName': other_type}]}]}]})
            if request.url.path == '/v3/items/spec' and json.loads(request.content)['productTypes'] == [other_type]:
                schema = copy.deepcopy(remote.schema)
                visible = schema['properties']['MPItem']['items']['properties']['Visible']
                visible['properties'][other_type] = visible['properties'].pop(PT)
                visible['required'] = [other_type]
                return httpx.Response(200, json={'schema': schema})
            return original_handler(request)
        client.app.state.walmart_publisher = WalmartPublishingClient(httpx.MockTransport(handler))
        doc = ready(fixture)
        first, second, *_ = doc['rows']
        # Six reviewed children: conflicting saved choices mixed with blanks.
        incomplete = copy.deepcopy(doc['rows'])
        for row, pt in zip(incomplete, [PT, other_type, '', '', PT, '']):
            row['product_type'] = pt
        with catalog.connect() as db:
            db.execute('UPDATE walmart_listing_documents SET rows_json=?,validation_json=? WHERE batch_id=?',
                       (json.dumps(incomplete), '{}', batch['id']))
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
                expect(table.locator('thead')).to_contain_text('ASIN / 店铺 SKU')
                expect(page.locator('.listing-sku-sidebar')).not_to_contain_text(first['source_sku'])
                table_first = table.locator(f'[data-listing-path="variants.{first["platform_sku"]}.visible.color"] input')
                table_second = table.locator(f'[data-listing-path="variants.{second["platform_sku"]}.visible.color"] input')
                detail = page.locator('#listing-required [data-listing-path="visible.color"] input')
                expect(detail).to_be_enabled()
                type_picker = page.get_by_label('沃尔玛商品类型', exact=True)
                expect(type_picker).to_have_value(PT)
                expect(table).not_to_contain_text('沃尔玛商品类型不一致')
                repaired = client.get(base).json()
                assert len(repaired['rows']) == 6
                assert {row['product_type'] for row in repaired['rows']} == {PT}
                assert not repaired['variant_groups'][0]['errors'], repaired['variant_groups'][0]['errors']
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
                asin = next(v['asin'] for item in doc['basis']['items'] for v in item['variants']
                            if v['platform_sku'] == second['platform_sku'])
                page.get_by_role('button', name='填写变体 ASIN ' + asin, exact=True).click()
                expect(type_picker).to_have_value(PT)
                expect(detail).to_have_value('Blue')
                detail.fill('Child editor Navy')
                expect(table_second).to_have_value('Child editor Navy')
                expect(table_first).to_have_value('Table Coffee')
                page.get_by_role('button', name='查看商品档案 ASIN ' + asin, exact=True).click()
                dialog = page.locator('.product-info-dialog')
                expect(dialog).to_be_visible()
                expect(dialog.locator('.product-info-facts')).to_contain_text(asin)
                page.get_by_role('button', name='关闭商品档案', exact=True).click()
                type_picker.select_option(other_type)
                expect(type_picker).to_be_enabled()
                expect(detail).to_be_visible()
                saved = client.get(base).json()
                assert {row['product_type'] for row in saved['rows']} == {other_type}
                first_asin = next(v['asin'] for item in doc['basis']['items'] for v in item['variants']
                                  if v['platform_sku'] == first['platform_sku'])
                page.get_by_role('button', name='填写变体 ASIN ' + first_asin, exact=True).click()
                expect(type_picker).to_have_value(other_type)
                expect(detail).to_have_value('Table Coffee')
                table.scroll_into_view_if_needed()
                output = Path(os.environ.get('CATALOG_TEST_OUTPUT', '/tmp/jiwu-variant-editor'))
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / 'shared-color.png'))
                assert not errors, errors
                browser.close()
            print('Passed: color-only columns, shared attributes, ASIN identity, legacy type repair, family category editing and fields.')
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            catalog.close()


if __name__ == '__main__':
    main()
