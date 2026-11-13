import csv
import io

from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.giga import sync_page
from catalog.giga_patrol import run_patrol
from test_giga import FakeClient, settings
from test_catalog import listing, bundle


def test_zero_source_stock_filter_pagination_export_and_patrol(tmp_path, settings):
    app = create_app(tmp_path/'stock.db'); c = app.state.catalog; client = TestClient(app)
    fake = FakeClient(('ZERO-A', 'ZERO-B', 'POSITIVE', 'UNKNOWN'))
    for row, qty in zip(fake.stock, [0, 0, 8, None]):
        row['sellerInventoryInfo']['sellerAvailableInventory'] = qty
    sync_page(c, settings, 1, client=fake)
    c.ingest(bundle([listing()]))
    query = {'source':'GigaB2B','inventory_status':'out_of_stock','page_size':1}
    first = client.get('/api/products', params=query).json()
    second = client.get('/api/products', params={**query, 'page':2}).json()
    assert first['total'] == second['total'] == 2
    assert {first['items'][0]['sku'], second['items'][0]['sku']} == {'ZERO-A','ZERO-B'}
    assert not client.get('/api/products', params={**query, 'page':3}).json()['items']
    assert client.get('/api/products', params={**query, 'q':'ZERO-A'}).json()['total'] == 1
    assert client.get('/api/products', params={**query, 'source':'Amazon'}).json()['total'] == 0
    assert client.get('/api/products', params={'source':'GigaB2B'}).json()['total'] == 4
    exported = client.get('/api/export.csv',params=query)
    rows = list(csv.reader(io.StringIO(exported.content.decode('utf-8-sig'))))
    assert {row[1] for row in rows[1:]} == {'ZERO-A','ZERO-B'}
    zero = c.find(q='ZERO-A')['items'][0]; item = c.get(zero['id'])
    changed = client.patch('/api/products/'+zero['id']+'/edits',json={
        'expected_revision':item['local_edit']['revision'], 'field':'stock','value':'10'})
    assert changed.status_code == 200
    assert c.find(inventory_status='out_of_stock')['total'] == 2
    fake.stock[0]['sellerInventoryInfo']['sellerAvailableInventory'] = 5
    run_patrol(c, settings, fake)
    remaining = c.find(inventory_status='out_of_stock')
    assert remaining['total'] == 1 and remaining['items'][0]['sku'] == 'ZERO-B'
    assert client.get('/api/products',params={'inventory_status':'typo'}).status_code == 422
    assert client.get('/api/export.csv',params={'inventory_status':'typo'}).status_code == 422
