import csv
import io

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.giga import GigaCanadaSettings, sync_page
from catalog.giga_patrol import run_patrol
from catalog.walmart_publishing import check_rows
from test_auth import team, login, ORIGIN
from test_giga import FakeClient, settings
from test_prelisting import inputs


@pytest.fixture
def canada(monkeypatch, settings):
    # Even identical credentials must not collapse the two regional identities.
    monkeypatch.setenv('CATALOG_GIGA_CA_CLIENT_ID', settings.client_id)
    monkeypatch.setenv('CATALOG_GIGA_CA_CLIENT_SECRET', 'canada-test-secret')
    monkeypatch.setenv('CATALOG_GIGA_CA_ENVIRONMENT', 'sandbox')
    return GigaCanadaSettings()


def test_regions_keep_separate_identity_filters_and_exports(tmp_path, settings, canada):
    app = create_app(tmp_path / 'regions.db')
    catalog, client = app.state.catalog, TestClient(app)
    us, ca = FakeClient(), FakeClient(currency='CAD')
    ca.prices[0]['price'] = '70'
    sync_page(catalog, settings, 1, client=us)
    sync_page(catalog, canada, 1, client=ca)
    assert settings.namespace != canada.namespace
    assert sync_page(catalog, canada, 1, client=ca)['unchanged'] == 2
    us_items = catalog.find(source='GigaB2B')['items']
    response = client.get('/api/products', params={'source': 'GigaB2B_CA', 'category': 'type:Chairs'})
    assert response.status_code == 200
    ca_items = response.json()['items']
    assert len(ca_items) == len(us_items) == 2
    assert {i['id'] for i in ca_items}.isdisjoint(i['id'] for i in us_items)
    assert all(i['currency'] == 'CAD' and i['giga_summary'] for i in ca_items)
    assert client.get('/api/stats', params={'source': 'GigaB2B_CA'}).json()['products'] == 2
    assert catalog.stats()['products'] == 4
    filters = {'source': 'GigaB2B_CA', 'inventory_status': 'out_of_stock'}
    zero = client.get('/api/products', params=filters).json()
    assert zero['total'] == 1 and zero['items'][0]['sku'] == 'W1'
    detail = client.get('/api/products/' + zero['items'][0]['id']).json()
    assert detail['source_facts']['region'] == 'CA'
    assert detail['price'] == '70' and detail['giga_summary']
    exported = client.get('/api/export.csv', params=filters)
    rows = list(csv.reader(io.StringIO(exported.content.decode('utf-8-sig'))))
    assert len(rows) == 2 and rows[1][5] == 'GigaB2B_CA'


def test_canada_patrol_changes_only_canada_and_never_writes_walmart(tmp_path, settings, canada, monkeypatch):
    app = create_app(tmp_path / 'patrol-regions.db')
    catalog, client = app.state.catalog, TestClient(app)
    sync_page(catalog, settings, 1, client=FakeClient())
    ca = FakeClient(currency='CAD')
    sync_page(catalog, canada, 1, client=ca)
    us_before = [i for i in catalog.export() if i['source'] == 'GigaB2B']
    ca.prices[0]['price'] = '80'
    ca.stock[1]['sellerInventoryInfo']['sellerAvailableInventory'] = 0
    def no_walmart(*args, **kwargs):
        pytest.fail('Canada patrol must not call US Walmart sync')
    monkeypatch.setattr('catalog.giga_walmart_sync.sync_batch', no_walmart)
    result = run_patrol(catalog, canada, ca)
    assert result['status'] == 'completed' and result['checked'] == 2
    assert result['price_changed'] == result['stock_changed'] == 1
    assert [i for i in catalog.export() if i['source'] == 'GigaB2B'] == us_before
    assert catalog.find(source='GigaB2B_CA', price_change='up')['total'] == 1
    path = '/api/integrations/giga-canada/patrol'
    assert client.get(path).json()['runs'][0]['id'] == result['id']
    assert client.get(path + '/' + result['id']).status_code == 200
    assert client.get('/api/integrations/giga/patrol/' + result['id']).status_code == 404
    assert client.get('/api/integrations/giga/patrol').json()['runs'] == []


def test_canada_routes_use_own_credentials_and_permissions(team, canada, monkeypatch):
    path = '/api/integrations/giga-canada'
    assert TestClient(team, base_url=ORIGIN).get(path).status_code == 401
    viewer, _ = login(team, 'viewer')
    assert viewer.post(path + '/sync', json={'page': 1}).status_code == 403
    editor, _ = login(team, 'editor')
    status = editor.get(path)
    assert status.json()['region'] == 'CA' and status.json()['configured']
    assert canada.client_id not in status.text and canada.secret not in status.text
    used = []
    def capture(catalog, configured, page, actor):
        used.append(configured)
        return {'created': 1}
    monkeypatch.setattr('catalog.giga.sync_page', capture)
    assert editor.post(path + '/sync', json={'page': 1}).json() == {'created': 1}
    assert used[0].namespace == canada.namespace and used[0].secret == canada.secret
    monkeypatch.delenv('CATALOG_GIGA_CA_CLIENT_SECRET')
    assert editor.post(path + '/sync', json={'page': 1}).status_code == 503
    assert editor.get('/api/integrations/giga').json()['configured']


def test_canada_cannot_create_us_prelisting(tmp_path, canada):
    app = create_app(tmp_path / 'plans.db')
    sync_page(app.state.catalog, canada, 1, client=FakeClient(currency='CAD'))
    client = TestClient(app)
    response = client.post('/api/prelisting/preview', json=inputs(client, filters={'source': 'GigaB2B_CA'}))
    assert response.status_code == 409 and '加拿大' in response.json()['detail']


def test_publishing_rejects_canada_before_us_api_access(monkeypatch):
    monkeypatch.setattr('catalog.walmart_publishing.unchanged_sources',
                        lambda *a: {'items': [{'source': 'GigaB2B_CA'}]})
    with pytest.raises(HTTPException) as caught:
        check_rows(None, None, None, None)
    assert caught.value.status_code == 409
