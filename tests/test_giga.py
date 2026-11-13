import copy
import json

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from catalog.giga import (GigaSettings, GigaClient, GigaError, sign, sync_page,
                          sync_lease, listing_from_api, PATHS)
from catalog.db import Catalog
from catalog.app import create_app
from test_auth import team, login, ORIGIN


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv('CATALOG_GIGA_CLIENT_ID', 'test-client')
    monkeypatch.setenv('CATALOG_GIGA_CLIENT_SECRET', 'test-secret')
    monkeypatch.setenv('CATALOG_GIGA_ENVIRONMENT', 'sandbox')
    return GigaSettings()


class FakeClient:
    def __init__(self, skus=('W1', 'W2'), currency='USD'):
        self.skus = list(skus)
        self.detail = [{'sku': s, 'productName': 'Chair '+s, 'description': '<p>Wood chair</p>',
                        'characteristics': ['Solid wood'], 'mainImageUrl': 'https://example.com/'+s+'.jpg',
                        'imageUrls': ['https://example.com/'+s+'.jpg'],
                        'category': 'Chairs', 'sellerInfo': {'sellerStore': 'Supplier '+s},
                        'comboInfo': [{'sku': 'part-'+s, 'qty': 2}], 'associateProductList': ['associate'],
                        'skuAvailable': True} for s in skus]
        self.prices = [{'sku': s, 'price': str(40+i), 'discountedPrice': '37.99',
                        'shippingFee': '4.99', 'currency': currency} for i, s in enumerate(skus)]
        self.stock = [{'sku': s, 'sellerInventoryInfo': {'sellerAvailableInventory': i},
                       'buyerInventoryInfo': {'totalBuyerAvailableInventory': 99}} for i, s in enumerate(skus)]
        self.pages = 1
        self.calls = []

    def query(self, operation, body):
        self.calls.append((operation, body))
        if operation == 'list':
            return {'pageInfo': {'page': body['page'], 'totalPage': self.pages},
                    'records': [{'sku': s} for s in self.skus]}
        # Reverse responses to catch positional joins.
        return copy.deepcopy({'detail': self.detail, 'price': self.prices[::-1], 'stock': self.stock[::-1]}[operation])


def test_signature_known_vector():
    # Independently calculated HMAC SHA-256 hex -> ASCII -> base64.
    assert sign('123456789', '123456789', '/api/v1/test', '1700000000000', '1234567890') == 'NDc3ZGFmZmVmNmE4ZjgyNGI3YTI4ZTA5M2RmOTkyMDQwYWZlOGMzOTFkYWJlMmU5NTBhNWFkNjM2ZGY2MzM0MQ=='


def test_sync_identity_idempotence_prices_stock_and_manual_edits(tmp_path, settings):
    app = create_app(tmp_path/'giga.db')
    catalog = app.state.catalog
    fake = FakeClient(currency='CAD')
    first = sync_page(catalog, settings, 1, client=fake)
    assert first['created'] == 2 and first['next_page'] is None
    assert catalog.stats()['products'] == 2
    rows = catalog.find()['items']
    assert {r['currency'] for r in rows} == {'CAD'}
    first_id = next(r['id'] for r in rows if r['sku'] == 'W1')
    item = catalog.get(first_id)
    assert item['asin'] == '' and item['price'] == '40' and item['stock'] == '0'
    assert item['source_facts']['shipping'] == '4.99' and not item['fill_complete']
    assert item['variants'] == [] and len(item['images']) == 1
    assert sync_page(catalog, settings, 1, client=fake)['unchanged'] == 2
    assert len(catalog.get(first_id)['history']) == 1
    response = TestClient(app).patch(f'/api/products/{first_id}/edits', json={
        'expected_revision': item['local_edit']['revision'], 'field': 'seller', 'value': 'Manually corrected seller'})
    assert response.status_code == 200, response.text
    fake.prices[0]['price'] = '44'
    fake.detail[0]['sellerInfo']['sellerStore'] = 'Updated supplier'
    assert sync_page(catalog, settings, 1, client=fake)['updated'] == 1
    updated = catalog.get(first_id)
    assert updated['seller'] == 'Manually corrected seller'
    assert updated['source_facts']['seller'] == 'Updated supplier'
    assert updated['price'] == '44' and updated['stock'] == '0'
    assert len(updated['history']) == 2


@pytest.mark.parametrize('failure', ['missing', 'duplicate', 'wrong', 'negative', 'fraction', 'bad-image'])
def test_bad_response_rolls_back_entire_page(tmp_path, settings, failure):
    c = Catalog(tmp_path/'giga.db'); fake = FakeClient()
    sync_page(c, settings, 1, client=fake)
    fake.prices[0]['price'] = '999'
    if failure == 'missing': fake.stock.pop()
    if failure == 'duplicate': fake.stock[1] = fake.stock[0]
    if failure == 'wrong': fake.detail[0]['sku'] = 'different'
    if failure == 'negative': fake.stock[0]['sellerInventoryInfo']['sellerAvailableInventory'] = -1
    if failure == 'fraction': fake.stock[0]['sellerInventoryInfo']['sellerAvailableInventory'] = 1.5
    if failure == 'bad-image': fake.detail[0]['mainImageUrl'] = 'javascript:alert(1)'
    with pytest.raises(GigaError): sync_page(c, settings, 1, client=fake)
    assert c.find(q='W1')['items'][0]['prices'] == ['40']
    with c.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM integration_leases').fetchone()[0] == 0


def test_account_isolation_pages_and_no_missing_stock_zero(tmp_path, settings, monkeypatch):
    c = Catalog(tmp_path/'giga.db'); fake = FakeClient(('W1',))
    fake.pages = 2
    fake.prices[0]['price'] = None
    fake.prices[0]['shippingFee'] = None
    fake.stock[0]['sellerInventoryInfo'] = {}
    result = sync_page(c, settings, 1, client=fake)
    assert result['next_page'] == 2
    item = c.get(c.find()['items'][0]['id'])
    assert item['stock'] is None and item['price'] is None
    assert item['source_facts']['shipping'] is None
    monkeypatch.setenv('CATALOG_GIGA_CLIENT_ID', 'other-account')
    sync_page(c, GigaSettings(), 1, client=fake)
    assert c.stats()['products'] == 2


def test_database_lease_and_reopen(tmp_path, settings):
    path=tmp_path/'giga.db'; c=Catalog(path)
    with sync_lease(c, settings.namespace):
        with pytest.raises(HTTPException) as exc:
            with sync_lease(Catalog(path), settings.namespace): pass
        assert exc.value.status_code == 409
    with sync_lease(Catalog(path), settings.namespace): pass


def test_http_headers_redirect_and_errors_redacted(settings, monkeypatch):
    monkeypatch.setattr('catalog.giga.time.sleep', lambda _: None)
    captured = []
    def handler(request):
        captured.append(request)
        assert request.headers['client-id'] == settings.client_id
        assert len(request.headers['nonce']) == 10
        assert request.headers['sign'] == sign(settings.client_id, settings.secret, PATHS['list'], request.headers['timestamp'], request.headers['nonce'])
        assert request.url.host == 'openapi-sandbox.gigab2b.com'
        return httpx.Response(302, headers={'location':'https://evil.example'}, text=settings.secret)
    with pytest.raises(GigaError) as exc:
        GigaClient(settings, httpx.MockTransport(handler)).query('list', {})
    assert settings.secret not in str(exc.value) and len(captured) == 1
    def error(request):
        return httpx.Response(200, json={'success':False,'code':'401','msg':settings.secret})
    with pytest.raises(GigaError) as exc:
        GigaClient(settings, httpx.MockTransport(error)).query('list', {})
    assert settings.secret not in str(exc.value)


def test_http_retry_and_parse(settings, monkeypatch):
    monkeypatch.setattr('catalog.giga.time.sleep', lambda _: None)
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1: return httpx.Response(429)
        return httpx.Response(200, content=b'{"success":true,"code":"200","data":{"price":43.99}}')
    assert GigaClient(settings, httpx.MockTransport(handler)).query('price', {}) == {'price':'43.99'}
    assert len(calls) == 2
    assert calls[0].headers['nonce'] != calls[1].headers['nonce']


def test_giga_auth(team, settings, monkeypatch):
    outside=TestClient(team, base_url=ORIGIN)
    assert outside.get('/api/integrations/giga').status_code == 401
    viewer,_=login(team,'viewer')
    assert viewer.post('/api/integrations/giga/sync',json={'page':1}).status_code == 403
    editor,_=login(team,'editor')
    assert editor.post('/api/integrations/giga/sync',json={'page':1},headers={'X-CSRF-Token':''}).status_code == 403
    assert settings.secret not in editor.get('/api/integrations/giga').text
    monkeypatch.setattr('catalog.giga.sync_page',lambda *a: {'created':1})
    assert editor.post('/api/integrations/giga/sync',json={'page':1}).json() == {'created':1}
    assert editor.post('/api/integrations/giga/sync',json={'page':0}).status_code == 422
    monkeypatch.delenv('CATALOG_GIGA_CLIENT_SECRET')
    assert editor.post('/api/integrations/giga/sync',json={'page':1}).status_code == 503


def test_existing_database_upgrade_preserves_references(tmp_path, request):
    if request.config.getoption('--postgres-dsn'):
        pytest.skip('SQLite table rebuild path')
    import sqlite3
    from test_catalog import listing, bundle
    path = tmp_path/'old.db'; c = Catalog(path)
    c.ingest(bundle([listing()]))
    before = c.get(c.find()['items'][0]['id'])
    # Reconstruct the deployed v12 constraint, then exercise real upgrade.
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA legacy_alter_table=ON')
        db.execute('CREATE TABLE products_old(id TEXT PRIMARY KEY,source TEXT NOT NULL,asin TEXT NOT NULL,UNIQUE(source,asin))')
        db.execute('INSERT INTO products_old SELECT * FROM products')
        db.execute('DROP TABLE products')
        db.execute('ALTER TABLE products_old RENAME TO products')
        db.execute('DELETE FROM migrations WHERE version=13')
    upgraded = Catalog(path)
    assert upgraded.get(before['id']) == before
    with upgraded.connect() as db:
        assert db.execute('PRAGMA foreign_key_check').fetchone() is None
        assert db.execute('SELECT COUNT(*) FROM variants').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0] == 1


def test_category_filter_and_empty_list(tmp_path, settings):
    c=Catalog(tmp_path/'giga.db'); fake=FakeClient()
    sync_page(c, settings, 1, client=fake)
    assert c.find(category='type:Chairs')['total'] == 2
    assert c.stats()['categories'][0]['label'] == 'Chairs'
    class Empty:
        def query(self, op, body): return {'pageInfo':{'page':1,'totalPage':0},'records':[]}
    assert sync_page(c,settings,1,client=Empty())['count'] == 0
    assert c.stats()['products'] == 2  # disappearance is never deletion or stock=0
