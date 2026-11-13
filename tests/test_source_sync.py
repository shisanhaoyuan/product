import json
import pytest
from catalog.models import ImportBundle
from catalog.app import create_app
from fastapi.testclient import TestClient
from test_editing import local, detail, edit
from test_source_capture import page, offer
from test_prelisting import product


def seed(catalog):
    p = product(seller='', fields={'seller_name': '', 'source_shipping': '',
        'dc_fulfillmentType': 'seller', 'dc_price': '99.99',
        'dynamic_attributes': {'seller_name': {'val': ['未采集']}, 'Color': 'Blue'}})
    p.variants[0].seller = ''
    p.variants[0].attributes = {'sold_by': '', 'source_price': ''}
    other = p.variants[0].model_copy(deep=True)
    other.sku, other.asin = 'other', 'B999999999'
    p.variants.append(other)
    p.issues = [
        {'section': 'fields', 'key': 'seller'},
        {'section': 'fields', 'key': 'seller', 'sku': 'child-0', 'reason': 'missing'},
        {'section': 'fields', 'key': 'seller', 'sku': 'other'},
        {'section': 'fields', 'key': 'seller', 'sku': 'child-0', 'reason': 'persisted_value_mismatch'},
        {'section': 'pricing_checks', 'sku': 'child-0', 'actual': '1', 'expected': '2'},
    ]
    p.fill_complete = False
    p.observed_at = '2026-11-13T10:00:00+00:00'
    catalog.ingest(ImportBundle(namespace='test', label='missing', listings=[p]))


def capture(client, seller='Garden Store', sku='', location='10001'):
    p = detail(client)
    asin = next(v['asin'] for v in p['variants'] if v['sku'] == sku) if sku else p['asin']
    return client.post(f"/api/products/{p['id']}/source-capture/browser", json={
        'variant_sku': sku or None, 'source_url': f'https://www.amazon.com/dp/{asin}',
        'html': page(asin, offer(seller)).replace('10001', location)})


def test_capture_fills_shared_identity_and_aliases_persists_without_changing_prices(local):
    client, catalog = local
    seed(catalog)
    original = catalog.export()
    before = detail(client)
    result = capture(client)
    assert result.status_code == 200, result.text
    assert result.json()['sync']['filled']
    after = detail(TestClient(create_app(catalog.path)))
    assert after['seller'] == after['fields']['seller_name'] == 'Garden Store'
    assert after['fields']['dynamic_attributes'] == {
        'seller_name': {'val': ['Garden Store']}, 'Color': 'Blue'}
    child, other = after['variants']
    assert child['seller'] == child['attributes']['sold_by'] == 'Garden Store'
    assert child['attributes']['source_price'] == '37.99'
    assert after['fields']['source_shipping'] == '0.00'
    assert other == before['variants'][1]
    assert after['fields']['dc_fulfillmentType'] == 'seller'
    assert after['fields']['dc_price'] == '99.99'
    for old, new in [(before, after), (before['variants'][0], child)]:
        for key in ('price', 'stock', 'copy', 'images'):
            assert old[key] == new[key]
    assert len(after['resolved_issues']) == 2
    assert len(after['issues']) == 3
    assert not after['fill_complete'] and not after['readback_verified']
    assert catalog.export() == original
    assert client.get('/api/products?q=Garden%20Store').json()['total'] == 1
    with catalog.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM product_edit_events WHERE action='source_sync'").fetchone()[0] == 1
    revision = after['local_edit']['revision']
    assert capture(client).json()['sync']['filled'] == []
    assert detail(client)['local_edit']['revision'] == revision
    p = detail(client)
    assert client.post(f"/api/products/{p['id']}/review", json={
        'expected_revision': revision}).status_code == 200
    confirmed = detail(client)
    assert edit(client, {'field': 'seller'}, 'Garden Store').json()['manual_review']['valid']
    assert detail(client)['local_edit']['revision'] == confirmed['local_edit']['revision']
    assert edit(client, {'field': 'brand'}, 'Next', before['local_edit']['revision']).status_code == 409


def test_reuse_historical_capture_without_fetch_and_without_repeating_edits(local, monkeypatch):
    client, catalog = local
    seed(catalog)
    p = detail(client)
    assert capture(client).status_code == 200
    p = detail(client)
    assert client.post(f"/api/products/{p['id']}/edits/reset", json={
        'expected_revision': p['local_edit']['revision']}).status_code == 200
    assert detail(client)['seller'] == ''
    monkeypatch.setattr('catalog.source_capture.fetch_amazon_page', lambda *_: pytest.fail('must reuse history'))
    path = f"/api/products/{p['id']}/source-capture/sync"
    result = client.post(path, json={})
    assert result.status_code == 200, result.text
    assert detail(client)['seller'] == 'Garden Store'
    version = detail(client)['local_edit']['revision']
    assert client.post(path, json={}).json()['sync']['filled'] == []
    assert detail(client)['local_edit']['revision'] == version
    with catalog.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM source_captures').fetchone()[0] == 1


def test_other_sku_is_isolated_and_partial_capture_only_fills_known_facts(local):
    client, catalog = local
    seed(catalog)
    before = detail(client)
    assert capture(client, sku='other', location='90210').status_code == 200
    p = detail(client)
    assert p['seller'] == '' and p['variants'][0] == before['variants'][0]
    assert p['variants'][1]['seller'] == 'Garden Store'
    assert 'shipping' not in p['variants'][1]['source_facts']
    assert 'price' not in p['variants'][1]['source_facts']
    assert len(p['resolved_issues']) == 1


def test_concurrent_manual_seller_and_review_are_preserved_or_invalidated_correctly(local, monkeypatch):
    client, catalog = local
    seed(catalog)
    p = detail(client)
    assert client.post(f"/api/products/{p['id']}/review", json={
        'expected_revision': p['local_edit']['revision']}).status_code == 200
    def during_fetch(asin):
        assert edit(client, {'field': 'seller'}, 'Human Seller').status_code == 200
        return page(asin)
    monkeypatch.setattr('catalog.source_capture.fetch_amazon_page', during_fetch)
    assert client.post(f"/api/products/{p['id']}/source-capture", json={}).status_code == 200
    p = detail(client)
    assert p['seller'] == p['fields']['seller_name'] == 'Human Seller'
    assert p['manual_review'] is None
    # Captured facts cannot be written through the normal editor endpoint.
    assert edit(client, {'field': 'source_facts', 'key': 'price'}, '1').status_code == 422


def test_new_source_identity_never_inherits_captured_values(local):
    client, catalog = local
    seed(catalog)
    assert capture(client).status_code == 200
    raw = catalog.export()[0]
    raw['variants'][0]['asin'] = 'B111111111'
    raw['observed_at'] = '2026-11-14T10:00:00+00:00'
    from catalog.models import Listing
    with pytest.raises(ValueError, match='身份冲突'):
        catalog.ingest(ImportBundle(namespace='test', label='new identity', listings=[Listing.model_validate(raw)]))
    # Defense in depth if identity changes through a future importer or manual database repair.
    with catalog.connect() as db:
        db.execute('UPDATE listings SET data_json=? WHERE id=?',
                   (json.dumps(raw), detail(client)['id']))
        catalog.refresh(db, detail(client)['id'])
    p = detail(client)
    assert p['seller'] == 'Garden Store' and p['variants'][0]['seller'] == ''
    assert p['local_edit']['conflicts']
    assert len(p['issues']) == 4
