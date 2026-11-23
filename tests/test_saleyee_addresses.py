"""Product-specific address ingestion/filtering on both supported databases."""
import json

from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.db import Catalog
from catalog.prelisting import Filters
from catalog.saleyee import SyncInput, sync_page
from catalog.saleyee_addresses import summarize_addresses
from test_saleyee import FakeClient, settings
from test_prelisting import inputs, create


def address_fixture():
    fake = FakeClient()
    east = dict(PlatformStockCode='USS-1', StockCountryCode='US', StockProvince='GA',
                StockCity='FOREST PARK', StockAddress='2065 ANVIL BLOCK ROAD', StockPostCode='30297', Qty=33)
    west = dict(PlatformStockCode='USW-1', StockCountryCode='US', StockProvince='CA',
                StockCity='Ontario', StockAddress='2221 Remington Ave', StockPostCode='91762', Qty=19)
    returns = dict(WarehouseCode='US', ThirdpartyStockCode='RETURN-NJ', CountryCode='US', Province='NJ',
                   City='Fieldsboro', Address1='1170 Florence, Gate(102-125) Columbus Rd', PostCode='08505')
    for n, detail in enumerate(fake.details):
        detail['ProductReturnAddressList'] = [returns if n == 0 else returns | dict(City='Newark', Address1='Other return street')]
        detail['ProductSendAddressList'] = [dict(WarehouseCode='US-W', **{
            k: v for k, v in east.items() if k not in ('PlatformStockCode', 'Qty')})]
        fake.stock[n]['ProductInventorySiteList'][0]['ProductInventoryList'][0]['StockItemList'] = (
            [east, west] if n == 0 else [west | dict(Qty=0)])
        if n == 1:
            detail['ProductSendAddressList'] = []
    return fake


def seeded(tmp_path, settings):
    app = create_app(tmp_path/'addresses.db'); client = TestClient(app)
    fake = address_fixture()
    sync_page(app.state.catalog, settings, SyncInput(), client=fake)
    return client, app.state.catalog, fake


def test_collect_all_subwarehouses_and_return_addresses(tmp_path, settings):
    client, catalog, fake = seeded(tmp_path, settings)
    info = summarize_addresses(fake.details[0], fake.stock[0])
    assert [v['code'] for v in info['shipping_addresses']] == ['USS-1', 'USW-1']
    assert [v['quantity'] for v in info['shipping_addresses']] == [33, 19]
    assert '08505' in info['return_addresses'][0]['address']
    assert summarize_addresses(fake.details[1], fake.stock[1])['shipping_addresses'][0]['quantity'] == 0
    card = next(v for v in catalog.find(source='Saleyee')['items'] if v['sku'] == 'S1')
    facts = client.get('/api/products/'+card['id']).json()['source_facts']
    assert facts['shipping_addresses'] == info['shipping_addresses']
    assert facts['return_addresses'] == info['return_addresses']
    # The entire original supplier lists remain stored as evidence.
    with catalog.connect() as db:
        raw = json.loads(db.execute('SELECT data_json FROM listings WHERE id=?',(card['id'],)).fetchone()[0])
    assert raw['source_facts']['saleyee']['detail']['ProductReturnAddressList'] == fake.details[0]['ProductReturnAddressList']
    assert len(raw['source_facts']['saleyee']['inventory']['ProductInventorySiteList'][0]['ProductInventoryList'][0]['StockItemList']) == 2


def test_address_filter_options_combination_pagination_export_and_prelisting(tmp_path, settings):
    client, catalog, _ = seeded(tmp_path, settings)
    stats = client.get('/api/stats',params={'source':'Saleyee'}).json()
    shipping = stats['shipping_addresses']; returns = stats['return_addresses']
    assert len(shipping) == len(returns) == 2
    east = next(v for v in shipping if '30297' in v['label'])
    west = next(v for v in shipping if '91762' in v['label'])
    nj = next(v for v in returns if '08505' in v['label'])
    assert east['count'] == 1 and west['count'] == 2  # Includes a zero-stock warehouse.
    def skus(**filters):
        response = client.get('/api/products',params={'source':'Saleyee',**filters})
        assert response.status_code == 200, response.text
        return {v['sku'] for v in response.json()['items']}
    assert skus(shipping_address=east['value']) == {'S1'}
    assert skus(shipping_address=west['value']) == {'S1','S2'}
    assert skus(shipping_address=west['value'],return_address=nj['value']) == {'S1'}
    multi = json.dumps({'shipping_address':[east['value'],west['value']], 'return_address':[nj['value']]})
    assert skus(multi=multi) == {'S1'}
    assert skus(shipping_address=east['value'],q='S2') == set()
    assert client.get('/api/products',params={'shipping_address':'invalid'}).status_code == 422
    assert client.get('/api/products',params={'multi':json.dumps({'return_address':['invalid']})}).status_code == 422
    page = client.get('/api/products',params={'source':'Saleyee','shipping_address':east['value'],'page_size':1}).json()
    assert page['total'] == 1
    exported = client.get('/api/export.csv',params={'source':'Saleyee','multi':multi}).text
    assert 'Wood chair S1' in exported and 'Wood chair S2' not in exported
    batch,_ = create(client,inputs(client,filters={'source':'Saleyee','multi':multi}))
    assert [v['sku'] for v in batch['preview']['items']] == ['S1']
    assert Filters(shipping_address=east['value']).shipping_address == east['value']
    assert not client.get('/api/stats',params={'source':'Amazon'}).json().get('shipping_addresses')


def test_legacy_addresses_are_rebuilt_without_source_changes(tmp_path, settings):
    _, catalog, _ = seeded(tmp_path, settings)
    original = catalog.export()
    with catalog.connect() as db:
        db.execute('DELETE FROM migrations WHERE version=24')
        if catalog.postgres:
            for row in db.execute('SELECT id,data_json FROM current_listings').fetchall():
                data = json.loads(row['data_json'])
                for key in ('shipping_addresses','return_addresses'):
                    data['source_facts'].pop(key,None)
                db.execute('UPDATE current_listings SET data_json=? WHERE id=?',(json.dumps(data),row['id']))
    reopened = Catalog(catalog.path)
    assert len(reopened.stats(source='Saleyee')['shipping_addresses']) == 2
    assert reopened.export() == original
    assert Catalog(catalog.path).stats(source='Saleyee')['return_addresses'] == reopened.stats(source='Saleyee')['return_addresses']


def test_unknown_addresses_not_selectable_and_no_inventory_is_invented():
    fake = address_fixture()
    fake.stock[0]['ProductInventorySiteList'] = []
    info = summarize_addresses(fake.details[0], fake.stock[0])
    assert len(info['shipping_addresses']) == 1
    assert info['shipping_addresses'][0]['quantity'] is None
    fake.details[0]['ProductSendAddressList'] = [dict(WarehouseCode='US-W',StockCountryCode='US')]
    assert summarize_addresses(fake.details[0],fake.stock[0])['shipping_addresses'][0]['key'] == ''
