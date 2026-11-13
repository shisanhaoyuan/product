import copy
import json
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.db import Catalog
from catalog.giga import GigaError, GigaSettings, listing_from_api, sync_lease, sync_page
from catalog.giga_patrol import run_patrol
from catalog.models import ImportBundle
from test_giga import settings, FakeClient
from test_catalog import listing, bundle
from test_auth import team, login, ORIGIN


def seeded(tmp_path, settings):
    app=create_app(tmp_path/'patrol.db'); c=app.state.catalog
    fake=FakeClient()
    sync_page(c,settings,1,client=fake)
    c.ingest(bundle([listing()]))
    return app,c,fake


def raws(c):
    with c.connect() as db:
        return {r['sku']:json.loads(r['data_json']) for r in db.execute('SELECT * FROM listings')}


def test_only_price_stock_change_and_manual_overlay_survives(tmp_path,settings):
    app,c,fake=seeded(tmp_path,settings)
    p=c.get(c.find(q='W1',source='GigaB2B')['items'][0]['id'])
    res=TestClient(app).patch('/api/products/'+p['id']+'/edits',json={
        'expected_revision':p['local_edit']['revision'],'field':'stock','value':'10'})
    assert res.status_code==200
    before=raws(c)
    fake.prices[0].update(price='55.5', shippingFee='0',currency='CAD',sellerInfo={'sellerStore':'do not replace'})
    fake.stock[0]['sellerInventoryInfo']['sellerAvailableInventory']=0
    fake.stock[1]['sellerInventoryInfo']['sellerAvailableInventory']=123
    fake.calls=[]
    result=run_patrol(c,settings,fake)
    assert result['status']=='completed' and result['checked']==2
    assert result['price_changed']==1 and result['stock_changed']==1
    assert [op for op,_ in fake.calls]==['price','stock']
    after=raws(c)
    assert after['B012345678-p']==before['B012345678-p']
    for sku in ('W1','W2'):
        for key in before[sku]:
            if key not in ('price','stock','source_facts','observed_at'):
                assert before[sku][key]==after[sku][key],key
        assert before[sku]['source_facts']['giga']['detail']==after[sku]['source_facts']['giga']['detail']
        assert before[sku]['source_facts']['seller']==after[sku]['source_facts']['seller']
    assert after['W1']['price']=='55.5' and after['W1']['stock']=='0'
    assert after['W2']['stock']=='123'
    assert c.get(p['id'])['stock']=='10'
    assert c.get(p['id'])['source_facts']['inventory']==0
    history=len(c.get(p['id'])['history'])
    repeat=run_patrol(c,settings,fake)
    assert repeat['changed']==0 and repeat['checked']==2
    assert len(c.get(p['id'])['history'])==history
    client=TestClient(app)
    detail=client.get('/api/integrations/giga/patrol/'+result['id']).json()
    row=next(r for r in detail['items'] if r['sku']=='W1')
    assert row['before']['price']=='40' and row['after']['price']=='55.5'
    assert client.get('/api/integrations/giga/patrol').json()['runs'][0]['id']==repeat['id']


@pytest.mark.parametrize('failed_endpoint',['price','stock'])
def test_one_endpoint_fails_other_commits(tmp_path,settings,failed_endpoint):
    _,c,fake=seeded(tmp_path,settings)
    original=fake.query
    fake.prices[0]['price']='70'
    fake.stock[0]['sellerInventoryInfo']['sellerAvailableInventory']=15
    def query(op,body):
        if op==failed_endpoint:raise GigaError('SECRET NOT FOR LOGS')
        return original(op,body)
    fake.query=query
    result=run_patrol(c,settings,fake)
    assert result['status']=='partial' and result['failed']==2
    raw=raws(c)['W1']
    assert raw['price']==('40' if failed_endpoint=='price' else '70')
    assert raw['stock']==('0' if failed_endpoint=='stock' else '15')
    with c.connect() as db:
        assert 'SECRET' not in str([dict(r) for r in db.execute('SELECT * FROM giga_patrol_items')])


@pytest.mark.parametrize('failure',['missing','invalid','duplicate','wrong'])
def test_inventory_failures_never_become_zero(tmp_path,settings,failure):
    _,c,fake=seeded(tmp_path,settings)
    if failure=='missing':fake.stock.pop()
    if failure=='invalid':fake.stock[1]['sellerInventoryInfo']['sellerAvailableInventory']=None
    if failure=='duplicate':fake.stock[1]=fake.stock[0]
    if failure=='wrong':fake.stock[1]['sku']='WRONG'
    result=run_patrol(c,settings,fake)
    assert result['status']=='partial'
    assert raws(c)['W2']['stock']=='1'
    assert result['failed']>=1


def test_other_account_and_full_sync_exclusion(tmp_path,settings,monkeypatch):
    _,c,fake=seeded(tmp_path,settings)
    with sync_lease(c,settings.namespace):
        result=run_patrol(c,settings,fake)
    assert result['status']=='failed' and result['checked']==0
    with sync_lease(c,settings.namespace+'.patrol'):
        with pytest.raises(HTTPException):run_patrol(c,settings,fake)
    monkeypatch.setenv('CATALOG_GIGA_CLIENT_ID','different')
    result=run_patrol(c,GigaSettings(),fake)
    assert result['total']==0
    assert raws(c)['W2']['stock']=='1'


def test_stale_worker_cannot_write_and_report_interruption(tmp_path,settings):
    app,c,fake=seeded(tmp_path,settings)
    original=fake.query
    def expire(op,body):
        with c.connect() as db:
            db.execute('UPDATE integration_leases SET expires_at=0 WHERE name=?',(settings.namespace,))
        return original(op,body)
    fake.query=expire
    fake.prices[0]['price']='999'
    result=run_patrol(c,settings,fake)
    assert result['status']=='failed' and result['checked']==0
    assert raws(c)['W1']['price']=='40'
    with c.connect() as db:
        db.execute("UPDATE giga_patrol_runs SET status='running',finished_at='' WHERE id=?",(result['id'],))
    assert TestClient(app).get('/api/integrations/giga/patrol').json()['runs'][0]['status']=='interrupted'


def test_price_validation_currency_shipping_and_zero(tmp_path,settings):
    _,c,fake=seeded(tmp_path,settings)
    fake.prices[0].update(price='NaN')
    fake.prices[1].update(price='0',shippingFee=None)
    result=run_patrol(c,settings,fake)
    assert result['failed']==1
    data=raws(c)
    assert data['W1']['price']=='40'
    assert data['W2']['price']=='0' and data['W2']['source_facts']['shipping'] is None


def test_batch_limit_and_real_sku_order(tmp_path,settings):
    c=Catalog(tmp_path/'many.db')
    skus=[f'W{i:03d}' for i in range(101)]
    items=[listing_from_api(s,{'sku':s},{'sku':s,'price':'12','currency':'USD'},
                           {'sku':s,'sellerInventoryInfo':{'sellerAvailableInventory':1}},settings.namespace,'2026-11-13T00:00:00+00:00') for s in skus]
    c.ingest(ImportBundle(namespace=settings.namespace,label='many',listings=items))
    class Client:
        def __init__(self):self.calls=[]
        def query(self,op,body):
            self.calls.append((op,body['skus']))
            return [{'sku':s, **({'price':str(int(s[1:])+100),'currency':'USD'} if op=='price' else
                                {'sellerInventoryInfo':{'sellerAvailableInventory':int(s[1:])}})} for s in reversed(body['skus'])]
    client=Client(); result=run_patrol(c,settings,client)
    assert result['checked']==101 and result['status']=='completed'
    assert [len(skus) for _,skus in client.calls]==[100,100,1,1]
    assert raws(c)['W100']['stock']=='100' and raws(c)['W000']['price']=='100'


def test_auth_and_account_scoped_history(team,settings,monkeypatch):
    outside=TestClient(team,base_url=ORIGIN)
    assert outside.get('/api/integrations/giga/patrol').status_code==401
    result=run_patrol(team.state.catalog,settings,FakeClient())
    viewer,_=login(team,'viewer')
    assert viewer.get('/api/integrations/giga/patrol').status_code==200
    assert viewer.get('/api/integrations/giga/patrol/'+result['id']).status_code==200
    monkeypatch.setenv('CATALOG_GIGA_CLIENT_ID','other')
    assert viewer.get('/api/integrations/giga/patrol/'+result['id']).status_code==404
    assert viewer.get('/api/integrations/giga/patrol').json()['runs']==[]
