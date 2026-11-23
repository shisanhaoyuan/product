"""Inclusive library filters use visible prices, upstream availability and same-child matches."""
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.db import Catalog
from catalog.models import ImportBundle, Listing
from catalog.prelisting import PlanInput, selected_rows
from catalog.product_ranges import metrics
from test_prelisting import inputs


def range_fixture():
    def row(sku, source, price=None, stock=None, facts=None, variants=None):
        asin = {'AMAZON':'B000000010','SINGLE':'B000000011','INVALID':'B000000012'}.get(sku, '')
        return Listing(external_id=sku, source=source, sku=sku, asin=asin, template=sku, store='fixture',
                       observed_at='2026-11-23T00:00:00+00:00', uploaded_at='2026-11-23',
                       evidence_kind='source_only', price=price, stock=stock,
                       source_facts=facts or {}, variants=variants or [], copy={'title':'Product '+sku})
    def giga(unit, fee, quantity, region='USD', fee_range=None):
        return dict(giga=dict(price=dict(price=unit,shippingFee=fee,currency=region,shippingFeeRange=fee_range or {}),
                             inventory=dict(sellerInventoryInfo=dict(sellerAvailableInventory=quantity))))
    return ImportBundle(namespace='ranges', label='fixtures', listings=[
        row('US', 'GigaB2B', '67', '999', giga('67','2.33',33)),
        row('US-RANGE', 'GigaB2B', '90', '999', giga('90','5',0,fee_range={'minAmount':'5','maxAmount':'15'})),
        row('CA', 'GigaB2B_CA', '50', '999', giga('50','5',5,'CAD')),
        row('SALEYEE', 'Saleyee', '22.50', '999', {'inventory':19,'landed':'22.50'}),
        row('UNKNOWN', 'Saleyee', None, '999', {'inventory':None}),
        row('AMAZON', 'Amazon', '999', '999', variants=[
            dict(sku='zero',asin='B000000001',price='9.99',stock='0'),
            dict(sku='positive',asin='B000000002',price='100.25',stock='15')]),
        row('SINGLE', 'Amazon', '20', '5'),
        row('INVALID', 'Amazon', 'NaN', '-1'),
    ])


@pytest.fixture
def local(tmp_path):
    app=create_app(tmp_path/'ranges.db')
    app.state.catalog.ingest(range_fixture())
    return TestClient(app), app.state.catalog


def skus(client, **params):
    response=client.get('/api/products',params=params)
    assert response.status_code==200, response.text
    return {p['sku'] for p in response.json()['items']}


def test_all_sources_status_inclusive_and_open_ranges(local):
    client,_=local
    assert skus(client,inventory_status='in_stock')=={'US','CA','SALEYEE','AMAZON','SINGLE'}
    assert skus(client,inventory_status='out_of_stock')=={'US-RANGE','AMAZON'}
    assert skus(client,stock_min=5,stock_max=19)=={'CA','SALEYEE','AMAZON','SINGLE'}
    assert skus(client,stock_min=33)=={'US'}
    assert skus(client,stock_max=0)=={'US-RANGE','AMAZON'}
    assert skus(client,stock_min=0,stock_max=0)=={'US-RANGE','AMAZON'}
    assert skus(client,source='GigaB2B_CA',inventory_status='in_stock')=={'CA'}
    assert skus(client,source='Saleyee',stock_min=1)=={'SALEYEE'}
    # The same parent can expose both available and zero-stock children.
    assert skus(client,source='Amazon',inventory_status='out_of_stock')=={'AMAZON'}


def test_display_price_exact_cents_and_giga_interval_overlap(local):
    client,_=local
    assert skus(client,price_min='69.33',price_max='69.33')=={'US'}
    assert skus(client,price_min='67',price_max='67')==set()  # Unit price is not displayed total.
    assert skus(client,price_min='69.34',price_max='94.99')==set()
    assert skus(client,price_min='95',price_max='95')=={'US-RANGE'}
    assert skus(client,price_min='105',price_max='105')=={'US-RANGE'}
    assert skus(client,price_min='99',price_max='100')=={'US-RANGE'}
    assert skus(client,price_min='105.01')==set()
    assert skus(client,price_max='22.50')=={'SALEYEE','AMAZON','SINGLE'}
    assert skus(client,source='GigaB2B_CA',price_min='55',price_max='55')=={'CA'}
    assert skus(client,price_min=0,price_max=0)==set()  # Missing/NaN never casts to zero.


def test_combined_criteria_must_match_the_same_child_including_multiselect(local):
    client,_=local
    assert skus(client,source='Amazon',stock_min=1,price_max='10')==set()
    assert skus(client,source='Amazon',inventory_status='in_stock',price_max='10')==set()
    multi=json.dumps({'inventory_status':['in_stock']})
    assert skus(client,source='Amazon',multi=multi,price_max='10')==set()
    multi=json.dumps({'inventory_status':['in_stock','out_of_stock']})
    assert skus(client,source='Amazon',multi=multi,stock_max=0,price_max='10')=={'AMAZON'}
    assert skus(client,source='Amazon',stock_min=15,price_min='100.25',price_max='100.25')=={'AMAZON'}


def test_filters_apply_before_pagination_export_and_cross_page_selection(local):
    client,catalog=local
    params=dict(inventory_status='in_stock',stock_min=5,stock_max=19,price_max='60')
    assert skus(client,**params)=={'CA','SALEYEE','SINGLE'}
    pages=[client.get('/api/products',params=params|{'page_size':1,'page':i}).json() for i in range(1,4)]
    assert all(p['total']==3 for p in pages)
    assert {p['items'][0]['sku'] for p in pages}=={'CA','SALEYEE','SINGLE'}
    exported=client.get('/api/export.csv',params=params).text
    assert all('Product '+sku in exported for sku in ('CA','SALEYEE','SINGLE'))
    assert all('Product '+sku not in exported for sku in ('US','UNKNOWN','AMAZON','INVALID'))
    data=PlanInput.model_validate(inputs(client,filters=params))
    with catalog.connect() as db:
        assert {r['sku'] for r in selected_rows(catalog,db,data)}=={'CA','SALEYEE','SINGLE'}
    assert skus(client,**params,q='SALEYEE')=={'SALEYEE'}


@pytest.mark.parametrize('params', [
    {'stock_min':-1}, {'stock_min':'1.5'}, {'stock_max':2147483648},
    {'stock_min':20,'stock_max':10}, {'price_min':-1}, {'price_min':'NaN'},
    {'price_min':'Infinity'}, {'price_min':'1.001'}, {'price_max':'10000000000'},
    {'price_min':20,'price_max':10}, {'price_min':'junk'},
])
def test_invalid_ranges_rejected_consistently(local,params):
    client,_=local
    assert client.get('/api/products',params=params).status_code==422
    assert client.get('/api/export.csv',params=params).status_code==422
    assert client.post('/api/prelisting/preview',json=inputs(client,filters=params)).status_code==422


def test_local_edits_source_refresh_and_old_catalog_migration(local):
    client,catalog=local
    before=catalog.export()
    us=next(p for p in catalog.find()['items'] if p['sku']=='US')
    detail=catalog.get(us['id'])
    response=client.patch('/api/products/'+us['id']+'/edits',json={
        'expected_revision':detail['local_edit']['revision'],'field':'stock','value':'0'})
    assert response.status_code==200
    assert skus(client,source='GigaB2B',stock_min=33)=={'US'}  # Supplier quantity ignores ERP edits.
    amazon=next(p for p in catalog.find()['items'] if p['sku']=='AMAZON')
    detail=catalog.get(amazon['id'])
    response=client.patch('/api/products/'+amazon['id']+'/edits',json={
        'expected_revision':detail['local_edit']['revision'],'field':'price','variant_sku':'positive','value':'8.99'})
    assert response.status_code==200
    assert skus(client,source='Amazon',stock_min=1,price_max='10')=={'AMAZON'}
    assert catalog.export()==before
    with catalog.connect() as db:
        db.execute('DELETE FROM migrations WHERE version=25')
        if catalog.postgres:
            db.execute("UPDATE current_listings SET filter_metrics_json='[]'")
    reopened=Catalog(catalog.path)
    assert reopened.find(source='Amazon',stock_min=1,price_max=Decimal('10'))['total']==1
    assert reopened.export()==before
    newer=range_fixture()
    for row in newer.listings:
        row.observed_at='2026-11-24T00:00:00+00:00'
        if row.sku=='US':
            row.source_facts['giga']['inventory']['sellerInventoryInfo']['sellerAvailableInventory']=0
    reopened.ingest(newer)
    assert reopened.find(source='GigaB2B',inventory_status='in_stock')['total']==0
    reopened.close()


def test_unknown_and_invalid_stock_remain_unknown():
    for value in (None, '', -1, 'NaN', 'Infinity', True, '2.5'):
        assert metrics({'source':'Amazon','stock':value,'price':None})==[dict(stock=None,price_low=None,price_high=None)]
