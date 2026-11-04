import csv
import io

import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.db import Catalog
from catalog.models import ImportBundle
from test_categories import item
from test_auth import team, login, ORIGIN


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.delenv('CATALOG_AUTH_ENABLED', raising=False)
    app = create_app(tmp_path / 'blacklist.db')
    return TestClient(app), app.state.catalog


def test_matches_variants_platform_and_persistence(local):
    client, catalog = local
    products = [
        item(0, seller='  Bad   SELLER '),
        item(1, seller='Good seller', variants=[{'sku': 'child-a', 'seller': 'Ｂａｄ Seller'}, {'sku': 'child-b', 'seller': 'Bad Seller'}]),
        item(2, seller='Bad Seller', source='eBay'),
        item(3, seller='Bad Seller Plus'), item(4, seller=''),
    ]
    bundle = ImportBundle(namespace='test', label='test', listings=products)
    catalog.ingest(bundle)
    before = catalog.export()
    entry = client.post('/api/seller-blacklist', json={'source': 'amazon', 'seller': 'Bad Seller', 'reason': '多次缺货\n发货延迟'}).json()
    assert entry['active'] == 1
    response = client.get('/api/products', params={'seller_status': 'blocked', 'page_size': 1}).json()
    assert response['total'] == 2 and len(response['items']) == 1
    assert len(response['items'][0]['seller_blacklist']) == 1
    second = client.get('/api/products', params={'seller_status': 'blocked', 'page_size': 1, 'page': 2}).json()
    assert second['items'][0]['id'] != response['items'][0]['id']
    detail = client.get('/api/products/' + second['items'][0]['id']).json()
    assert detail['seller_blacklist'][0]['reason'] == entry['reason']
    assert client.get('/api/products', params={'seller_status': 'unblocked'}).json()['total'] == 3
    assert client.get('/api/products', params={'seller_status': 'blocked', 'q': 'sku-2'}).json()['total'] == 0
    csv_rows = list(csv.DictReader(io.StringIO(client.get('/api/export.csv?seller_status=blocked').text.lstrip('\ufeff'))))
    assert len(csv_rows) == 2 and all('多次缺货' in r['卖家黑名单原因'] for r in csv_rows)
    assert catalog.ingest(bundle)['unchanged'] == 5
    assert catalog.export() == before
    reopened = Catalog(catalog.path)
    assert reopened.find(seller_status='blocked')['total'] == 2
    assert reopened.get(detail['id'])['seller_blacklist'][0]['id'] == entry['id']
    assert client.post('/api/seller-blacklist', json={'source': 'AMAZON', 'seller': 'bad  seller', 'reason': 'other'}).status_code == 409
    updated = client.patch(f"/api/seller-blacklist/{entry['id']}", json={'reason': '<script>alert(1)</script> 原因', 'active': True})
    assert updated.status_code == 200
    assert reopened.get(detail['id'])['seller_blacklist'][0]['reason'] == '<script>alert(1)</script> 原因'
    assert client.patch(f"/api/seller-blacklist/{entry['id']}", json={'reason': '已沟通', 'active': False}).status_code == 200
    assert reopened.find(seller_status='blocked')['total'] == 0
    assert reopened.get(detail['id'])['seller_blacklist'] == []
    assert client.get('/api/seller-blacklist').json()['total'] == 0
    assert client.get('/api/seller-blacklist?status=inactive').json()['total'] == 1
    assert client.patch(f"/api/seller-blacklist/{entry['id']}", json={'reason': '再次缺货', 'active': True}).status_code == 200
    assert reopened.find(seller_status='blocked')['total'] == 2
    assert catalog.export() == before
    with catalog.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM seller_blacklist_events').fetchone()[0] == 4
        assert db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0] == 5


def test_validation_and_search(local):
    client, _ = local
    valid = {'source': 'Amazon', 'seller': 'Seller', 'reason': '缺货'}
    for changes in [{'source': '  '}, {'seller': '\n'}, {'reason': ' \n'}, {'reason': 'x'*2001}]:
        assert client.post('/api/seller-blacklist', json=valid | changes).status_code == 422
    for i in range(3):
        assert client.post('/api/seller-blacklist', json=valid | {'seller': f'Seller {i}'}).status_code == 201
    assert client.get('/api/seller-blacklist?q=缺货&page_size=2').json()['total'] == 3
    assert len(client.get('/api/seller-blacklist?page_size=2&page=2').json()['items']) == 1
    assert client.get('/api/seller-blacklist', params={'q': "' OR 1=1--"}).json()['total'] == 0
    assert client.get('/api/seller-blacklist?status=bad').status_code == 422
    assert client.get('/api/products?seller_status=bad').status_code == 422
    assert client.patch('/api/seller-blacklist/999', json={'reason': 'x', 'active': True}).status_code == 404
    assert client.patch('/api/seller-blacklist/1', json={'reason': 'x', 'active': True, 'seller': 'changed'}).status_code == 422
    assert client.post('/api/seller-blacklist', json=valid, headers={'Origin': 'https://attacker.example'}).status_code == 403


@pytest.mark.parametrize('role', ['admin', 'editor', 'viewer'])
def test_blacklist_permissions(team, role):
    anonymous = TestClient(team, base_url=ORIGIN)
    assert anonymous.get('/api/seller-blacklist').status_code == 401
    admin, _ = login(team)
    entry = admin.post('/api/seller-blacklist', json={'source': 'Amazon', 'seller': 'Bad', 'reason': '缺货'}).json()
    client, _ = login(team, role)
    assert client.get('/api/seller-blacklist').status_code == 200
    expected = 403 if role == 'viewer' else 201
    assert client.post('/api/seller-blacklist', json={'source': 'Amazon', 'seller': 'Other', 'reason': '缺货'}).status_code == expected
    expected = 403 if role == 'viewer' else 200
    assert client.patch(f"/api/seller-blacklist/{entry['id']}", json={'reason': '修改', 'active': False}).status_code == expected
    client.headers.pop('X-CSRF-Token')
    assert client.post('/api/seller-blacklist', json={'source': 'Amazon', 'seller': 'No CSRF', 'reason': '缺货'}).status_code == 403
    if role != 'viewer':
        with team.state.catalog.connect() as db:
            assert db.execute('SELECT actor_id FROM seller_blacklist_events ORDER BY id DESC LIMIT 1').fetchone()[0] is not None
