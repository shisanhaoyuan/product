import copy
import json

import pytest
from fastapi.testclient import TestClient

from catalog.giga_presentation import summary
from catalog.giga import sync_page
from catalog.giga_patrol import run_patrol
from catalog.app import create_app
from test_giga import FakeClient, settings


def facts(**price):
    return {'giga': {'price': {'price': '67', 'shippingFee': '2.33', 'currency': 'USD', **price},
                     'detail': {}, 'inventory': {}}}


def test_total_keeps_regular_price_and_does_not_change_source():
    source = facts(discountedPrice='60', exclusivePrice='55', packingFee='2.33')
    before = copy.deepcopy(source)
    result = summary(source)
    assert result['estimated_totals'] == ['69.33']
    assert result['unit_price'] == '67.00'
    assert result['shipping_fee'] == '2.33'
    assert source == before
    assert summary(facts(price='0.1', shippingFee='0.2'))['estimated_totals'] == ['0.30']


@pytest.mark.parametrize('price', [dict(shippingFee=None), dict(shippingFee=''),
    dict(shippingFee='unknown'), dict(price=None), dict(price=True), dict(currency=' '),
    dict(price='NaN'), dict(shippingFee=-1)])
def test_missing_data_never_becomes_zero_or_guessed_total(price):
    assert summary(facts(**price))['estimated_totals'] == []


def test_shipping_range_preserves_uncertainty():
    assert summary(facts(shippingFeeRange={'minAmount':2.33,'maxAmount':10}))['estimated_totals'] == ['69.33','77.00']
    assert summary(facts(shippingFee=None, shippingFeeRange={'minAmount':2.33,'maxAmount':2.33}))['estimated_totals'] == ['69.33']
    for value in [{'minAmount':10,'maxAmount':2}, {'minAmount':2}, {'minAmount':'bad','maxAmount':2}]:
        assert summary(facts(shippingFeeRange=value))['estimated_totals'] == []
    assert summary(facts(shippingFeeRange={'minAmount':None,'maxAmount':None}))['estimated_totals'] == ['69.33']


@pytest.mark.parametrize('score,stock,returns,expected', [
    ('79.99',0,'High',['无可售库存','卖家评分低于 80','商品退货率高','卖家退货率高']),
    ('80',1,'Low',[]), (None,None,None,[]), ('N/A',True,'Moderate',[]), ('86.10',4,'0.9%',[]),
])
def test_risk_boundaries_and_unknowns(score,stock,returns,expected):
    source=facts()
    source['giga']['detail']={'sellerInfo':{'gigaIndex':score,'sellerReturnRate':returns},'returnRate':returns}
    source['giga']['inventory']={'sellerInventoryInfo':{'sellerAvailableInventory':stock}}
    result=summary(source)
    assert result['risks']==expected
    if stock is None or stock is True: assert result['inventory'] is None


def test_list_detail_and_patrol_project_same_total_without_rewriting_unit_price(tmp_path,settings):
    app=create_app(tmp_path/'view.db'); c=app.state.catalog; fake=FakeClient(('W1',))
    fake.prices[0].update(price='67',shippingFee='2.33')
    fake.detail[0]['sellerInfo'].update(gigaIndex='79.99',sellerReturnRate='High')
    sync_page(c,settings,1,client=fake)
    client=TestClient(app)
    row=client.get('/api/products',params={'source':'GigaB2B'}).json()['items'][0]
    item=client.get('/api/products/'+row['id']).json()
    assert row['giga_summary']==item['giga_summary']
    assert row['giga_summary']['estimated_totals']==['69.33']
    assert item['price']=='67' and row['prices']==['67']
    response=client.patch('/api/products/'+row['id']+'/edits',json={
        'expected_revision':item['local_edit']['revision'],'field':'price','value':'99'})
    assert response.status_code==200
    assert c.get(row['id'])['giga_summary']['estimated_totals']==['69.33']
    fake.prices[0]['price']='70'
    run_patrol(c,settings,fake)
    updated=c.get(row['id'])
    assert updated['price']=='99.00'
    assert updated['giga_summary']['estimated_totals']==['72.33']
    assert updated['giga_summary']['giga_index']=='79.99'
    with c.connect() as db:
        stored=json.loads(db.execute('SELECT data_json FROM listings WHERE id=?',(row['id'],)).fetchone()[0])
    assert stored['price']=='70' and 'giga_summary' not in stored
