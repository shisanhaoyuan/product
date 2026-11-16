import copy

import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.giga import sync_page
from test_giga import FakeClient, settings
from test_prelisting import inputs, create


@pytest.fixture
def warehouse(tmp_path, settings):
    app = create_app(tmp_path / 'giga-plans.db')
    fake = FakeClient(('W1', 'W2'))
    for p in fake.prices:
        p.update(price='67', shippingFee='2.33', packingFee='2.33', discountedPrice='37.99')
    for s in fake.stock:
        s['sellerInventoryInfo']['sellerAvailableInventory'] = 25
        s['warehouses'] = [{'warehouseCode': 'CA', 'inventory': 25}]
    for d in fake.detail:
        d['upc'] = '012345678905'
    sync_page(app.state.catalog, settings, 1, client=fake)
    return TestClient(app), app.state.catalog, fake, settings


def plan(client, **changes):
    return inputs(client, filters={'source': 'GigaB2B', 'deduplicate': True}, multiplier='1.15', **changes)


def test_multiple_no_asin_products_freeze_content_price_stock_and_confirm(warehouse):
    client, catalog, fake, _ = warehouse
    before = catalog.export()
    preview = client.post('/api/prelisting/preview', json=plan(client)).json()
    assert preview['total'] == preview['ready_count'] == 2 and preview['can_create']
    assert preview['execution_enabled'] is False
    for item in preview['items']:
        v = item['variants'][0]
        assert item['asin'] == '' and not item['errors']
        assert (v['unit_price'], v['shipping_fee'], v['base'], v['price'], item['msrp']) == ('67.00', '2.33', '69.33', '79.73', '159.46')
        assert v['stock'] == '10' and v['source_stock'] == 25
        assert v['content']['copy']['title'] == 'Chair ' + v['sku']
        assert v['content']['images'] == ['https://example.com/' + v['sku'] + '.jpg']
        assert v['source_facts']['giga']['inventory']['warehouses'][0]['warehouseCode'] == 'CA'
    batch, payload = create(client, plan(client))
    skus = [i['variants'][0]['platform_sku'] for i in batch['preview']['items']]
    assert len(set(skus)) == 2 and all(s.startswith('MY17') and len(s) == 10 for s in skus)
    assert client.post('/api/prelisting/batches', json=payload).json()['id'] == batch['id']
    assert client.post(f"/api/prelisting/batches/{batch['id']}/action", json={'action': 'confirm'}).json()['status'] == 'confirmed'
    assert catalog.export() == before  # No price, stock, content or evidence rewritten.
    reopened = TestClient(create_app(catalog.path))
    assert reopened.get('/api/prelisting/batches/' + batch['id']).json()['preview'] == batch['preview']


def test_same_store_duplicate_rejected_other_store_independent_cancel_reuses_sku(warehouse):
    client, _, _, _ = warehouse
    a, _ = create(client, plan(client))
    duplicate = client.post('/api/prelisting/preview', json=plan(client)).json()
    assert not duplicate['can_create'] and all('供应商 SKU' in i['errors'][0] for i in duplicate['items'])
    second_store = next(s for s in client.get('/api/listing-stores').json() if s['name'] == 'MYZ-L-18')
    b, _ = create(client, plan(client, store_id=second_store['id']))
    assert all(i['variants'][0]['platform_sku'].startswith('MY18') for i in b['preview']['items'])
    assert client.post(f"/api/prelisting/batches/{a['id']}/action", json={'action': 'cancel'}).status_code == 200
    c, _ = create(client, plan(client))
    assert [i['variants'][0]['platform_sku'] for i in a['preview']['items']] == [i['variants'][0]['platform_sku'] for i in c['preview']['items']]


@pytest.mark.parametrize('quantity, expected', [(0, '0'), (9, '0'), (10, '10'), (11, '10'), (None, None)])
def test_source_inventory_threshold_and_unknown_never_guessed(warehouse, quantity, expected):
    client, catalog, fake, settings = warehouse
    fake.stock[0]['sellerInventoryInfo']['sellerAvailableInventory'] = quantity
    sync_page(catalog, settings, 1, client=fake)
    result = client.post('/api/prelisting/preview', json=plan(client)).json()
    item = next(i for i in result['items'] if i['sku'] == 'W1')
    assert item['variants'][0]['stock'] == expected
    assert item['variants'][0]['source_stock'] == quantity
    assert item['status'] == ('ready' if quantity is not None and quantity >= 10 else 'review')


@pytest.mark.parametrize('failure', ['shipping', 'range', 'currency', 'upc', 'image', 'description', 'feature', 'unavailable'])
def test_incomplete_or_uncertain_source_creates_review_draft_but_cannot_confirm(warehouse, failure):
    client, catalog, fake, settings = warehouse
    if failure == 'shipping': fake.prices[0]['shippingFee'] = None
    if failure == 'range': fake.prices[0]['shippingFeeRange'] = {'minAmount': 2, 'maxAmount': 10}
    if failure == 'currency': fake.prices[0]['currency'] = 'CAD'
    if failure == 'upc': fake.detail[0]['upc'] = ''
    if failure == 'image': fake.detail[0].update(mainImageUrl='', imageUrls=[])
    if failure == 'description': fake.detail[0]['description'] = '<p>&nbsp; </p>'
    if failure == 'feature': fake.detail[0]['characteristics'] = ['<p> </p>']
    if failure == 'unavailable': fake.detail[0]['skuAvailable'] = False
    sync_page(catalog, settings, 1, client=fake)
    batch, _ = create(client, plan(client))
    item = next(i for i in batch['preview']['items'] if i['sku'] == 'W1')
    assert item['status'] == 'review' and item['errors']
    assert not any('ASIN' in e or 'ERP' in e for e in item['errors'])
    assert client.post(f"/api/prelisting/batches/{batch['id']}/action", json={'action': 'confirm'}).status_code == 409


def test_local_edits_do_not_reprice_already_multiplied_values_and_resync_invalidates_draft(warehouse):
    client, catalog, fake, settings = warehouse
    item = catalog.get(catalog.find(q='W1')['items'][0]['id'])
    response = client.patch('/api/products/' + item['id'] + '/edits', json={
        'expected_revision': item['local_edit']['revision'], 'field': 'price', 'value': '999'})
    assert response.status_code == 200
    item = response.json()
    response = client.patch('/api/products/' + item['id'] + '/edits', json={
        'expected_revision': item['local_edit']['revision'], 'field': 'copy.title', 'value': 'Edited chair'})
    assert response.status_code == 200
    batch, _ = create(client, plan(client))
    v = next(i for i in batch['preview']['items'] if i['sku'] == 'W1')['variants'][0]
    assert v['current_price'] == '999.00' and v['base'] == '69.33' and v['price'] == '79.73'
    assert v['content']['copy']['title'] == 'Edited chair'
    fake.prices[0]['price'] = '70'
    sync_page(catalog, settings, 1, client=fake)
    assert client.post(f"/api/prelisting/batches/{batch['id']}/action", json={'action': 'confirm'}).status_code == 409
