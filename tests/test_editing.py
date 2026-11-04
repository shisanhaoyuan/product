import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.models import ImportBundle
from test_auth import team, login, ORIGIN
from test_prelisting import product, inputs, create


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.delenv('CATALOG_AUTH_ENABLED', raising=False)
    app = create_app(tmp_path / 'edits.db')
    item = product()
    item.fields = {'dc_productType': 'Chairs', 'Color': 'Black', 'dc_platformSku': 'locked',
                   'nested': {'value': 'x'}}
    item.variants[0].attributes = {'Color': 'Black'}
    other = item.variants[0].model_copy(deep=True)
    other.sku = 'second-child'
    other.asin = 'B999999999'
    other.copy.title = 'Second child'
    item.variants.append(other)
    app.state.catalog.ingest(ImportBundle(namespace='test', label='test', listings=[item]))
    return TestClient(app), app.state.catalog


def detail(client):
    lid = client.get('/api/products').json()['items'][0]['id']
    return client.get('/api/products/' + lid).json()


def edit(client, target, value, revision=None):
    p = detail(client)
    return client.patch(f"/api/products/{p['id']}/edits", json={
        'expected_revision': revision or p['local_edit']['revision'], **target, 'value': value})


def test_edit_save_reload_list_and_source_unchanged(local):
    client, catalog = local
    before = catalog.export()
    with catalog.connect() as db:
        snapshots = [tuple(r) for r in db.execute('SELECT * FROM snapshots')]
    assert edit(client, {'field': 'copy.title'}, 'New handmade chair').status_code == 200
    assert edit(client, {'field': 'brand'}, 'NewBrand').status_code == 200
    assert edit(client, {'field': 'fields', 'key': 'dc_productType'}, 'Desks').status_code == 200
    assert edit(client, {'field': 'copy.features'}, ['first', '', 'second']).status_code == 200
    assert edit(client, {'field': 'copy.description'}, '<p>safe content</p>').status_code == 200
    assert edit(client, {'field': 'images'}, ['https://example.com/a.png']).status_code == 200
    p = detail(client)
    assert p['copy']['title'] == 'New handmade chair'
    assert p['copy']['features'] == ['first', '', 'second']
    assert p['category'] == 'Desks'
    assert p['local_edit']['changed'] and len(p['local_edit']['changes']) == 6
    assert not p['fill_complete'] and not p['save_succeeded'] and not p['readback_verified']
    found = client.get('/api/products', params={'brand': 'NewBrand', 'category': 'type:Desks', 'q': 'handmade'}).json()
    assert found['total'] == 1 and found['items'][0]['title'] == p['copy']['title']
    assert client.get('/api/products?status=review').json()['total'] == 1
    assert client.get('/api/stats').json()['brands'] == ['NewBrand']
    assert client.get('/api/stats').json()['categories'] == [{'value': 'type:Desks', 'label': 'Desks', 'count': 1}]
    assert 'New handmade chair' in client.get('/api/export.csv').text
    reopened = TestClient(create_app(catalog.path))
    assert detail(reopened)['copy'] == p['copy']
    assert catalog.export() == before
    with catalog.connect() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM snapshots')] == snapshots
    assert client.get(f"/api/products/{p['id']}/snapshots/{p['history'][0]['id']}").json()['copy']['title'] == 'Old chair'
    reset = client.post(f"/api/products/{p['id']}/edits/reset", json={'expected_revision': p['local_edit']['revision']})
    assert reset.status_code == 200 and not reset.json()['local_edit']['changed']
    assert reset.json()['fill_complete'] and reset.json()['copy']['title'] == 'Old chair'
    assert edit(client, {'field': 'brand'}, 'Old').json()['local_edit']['revision'] == reset.json()['local_edit']['revision']


def test_child_isolation_and_validation(local):
    client, catalog = local
    original = detail(client)
    assert edit(client, {'field': 'copy.title', 'variant_sku': 'child-0'}, 'Edited child').status_code == 200
    for field, val in [('price', '66.50'), ('stock', '0'), ('ean', '1234567890123'),
                       ('seller', 'Blocked seller'), ('images', ['https://example.com/child.png'])]:
        assert edit(client, {'field': field, 'variant_sku': 'child-0'}, val).status_code == 200
    assert edit(client, {'field': 'attributes', 'key': 'Color', 'variant_sku': 'child-0'}, 'Blue').status_code == 200
    p = detail(client)
    assert p['variants'][0]['price'] == '66.50' and p['variants'][0]['stock'] == '0'
    assert p['variants'][1] == original['variants'][1]
    assert p['variants'][0]['sku'] == original['variants'][0]['sku']
    assert p['variants'][0]['asin'] == original['variants'][0]['asin']
    client.post('/api/seller-blacklist', json={'source': 'Amazon', 'seller': 'Blocked seller', 'reason': 'test'})
    assert client.get('/api/products?seller_status=blocked').json()['total'] == 1
    for target, val in [({'field': 'asin'}, 'B999999999'), ({'field': 'sku'}, 'wrong'),
                         ({'field': 'fields', 'key': 'dc_platformSku'}, 'wrong'),
                         ({'field': 'fields', 'key': 'nested'}, 'flatten'),
                         ({'field': 'price'}, '-1'), ({'field': 'price'}, 'NaN'),
                         ({'field': 'price'}, '1.234'), ({'field': 'stock'}, '9'),
                         ({'field': 'ean'}, 'invalid'), ({'field': 'copy.features'}, 'not list'),
                         ({'field': 'images'}, ['javascript:alert(1)'])]:
        assert edit(client, target, val).status_code == 422
    assert edit(client, {'field': 'price', 'variant_sku': 'missing'}, '12').status_code == 409


def test_mirrors_and_removed_child(local):
    client, catalog = local
    original = catalog.export()[0]
    original.pop('id'); original.pop('namespace')
    original['fields'].update({'Brand Name': 'Old', 'dc_price': '50.00', 'MSRP': '100.00'})
    original['observed_at'] = '2026-11-04T11:00:00+00:00'
    catalog.ingest(ImportBundle.model_validate({'namespace': 'test', 'label': 'mirrors', 'listings': [original]}))
    for field, value, mirror in [('brand', 'New', 'Brand Name'), ('price', '60.00', 'dc_price'),
                                 ('msrp', '120.00', 'MSRP')]:
        p = edit(client, {'field': field}, value).json()
        assert p[field] == p['fields'][mirror] == value
    assert catalog.export()[0]['fields']['dc_price'] == '50.00'
    assert edit(client, {'field': 'price', 'variant_sku': 'child-0'}, '66.50').status_code == 200
    original['variants'] = [original['variants'][1]]
    original['observed_at'] = '2026-11-04T12:00:00+00:00'
    catalog.ingest(ImportBundle.model_validate({'namespace': 'test', 'label': 'removed', 'listings': [original]}))
    p = detail(client)
    assert p['local_edit']['conflicts'] == ['child-0']
    assert p['variants'] == original['variants']


def test_concurrency_reimport_and_stale_batch(local):
    client, catalog = local
    batch, _ = create(client, inputs(client))
    revision = detail(client)['local_edit']['revision']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda title: edit(client, {'field': 'copy.title'}, title, revision), ['A', 'B']))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert client.post(f"/api/prelisting/batches/{batch['id']}/action", json={'action': 'confirm'}).status_code == 409
    prior = detail(client)
    updated = catalog.export()[0]
    updated.pop('id'); updated.pop('namespace')
    updated['observed_at'] = '2026-11-04T12:00:00+00:00'
    updated['copy']['description'] = 'New upstream description'
    catalog.ingest(ImportBundle.model_validate({'namespace': 'test', 'label': 'update', 'listings': [updated]}))
    p = detail(client)
    assert p['copy']['title'] == prior['copy']['title']
    assert p['copy']['description'] == 'New upstream description'
    assert p['local_edit']['source_changed']
    assert edit(client, {'field': 'brand'}, 'Other', prior['local_edit']['revision']).status_code == 409
    with catalog.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM product_edit_events').fetchone()[0] == 1


@pytest.mark.parametrize('role', ['admin', 'editor', 'viewer'])
def test_edit_permissions(team, role):
    team.state.catalog.ingest(ImportBundle(namespace='test', label='test', listings=[product()]))
    client, _ = login(team, role)
    p = detail(client)
    payload = {'expected_revision': p['local_edit']['revision'], 'field': 'brand', 'value': 'New'}
    path = f"/api/products/{p['id']}/edits"
    assert TestClient(team, base_url=ORIGIN).patch(path, json=payload).status_code == 401
    assert client.patch(path, json=payload).status_code == (403 if role == 'viewer' else 200)
    if role == 'viewer':
        assert client.post(path + '/reset', json={'expected_revision': p['local_edit']['revision']}).status_code == 403
    client.headers.pop('X-CSRF-Token')
    assert client.patch(path, json=payload).status_code == 403
