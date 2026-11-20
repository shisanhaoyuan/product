import json
import pytest

from catalog.models import ImportBundle
from catalog.listing_price_review import MISSING
from catalog.walmart_publishing import source_issues
from test_walmart_publishing import publishing, ready, submission
from test_giga_prelisting import warehouse
from test_giga import settings
from test_walmart_accounts import vault
from test_prelisting import product, inputs, create
from test_auth import team, login


@pytest.fixture
def amazon(publishing):
    c,catalog,fake,settings,_,_,remote=publishing
    goods=[]
    for index in (70,71):
        p=product(index,brand='NewBrand')
        v=p.variants[0];v.source_facts={'asin':v.asin,'currency':'USD'}
        v.ean='012345678905' if index==70 else '012345678912'
        v.copy.title='Wood chair';v.copy.description='Wooden chair for home';v.copy.features=['Wood frame']
        v.images=['https://example.com/chair.jpg']
        goods.append(p)
    catalog.ingest(ImportBundle(namespace='test-amazon',label='test',listings=goods))
    b,_=create(c,inputs(c,filters={'source':'Amazon'}))
    base=f"/api/prelisting/batches/{b['id']}/walmart"
    assert c.post(base+'/prepare').status_code==200
    env=(c,catalog,fake,settings,b,base,remote)
    doc=c.get(base).json()
    for row in doc['rows']: row['orderable']['price']=102.05
    assert c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).status_code==200
    ready(env)
    return env


def confirm(c,base,doc,sku):
    return c.post(base+'/verify-source-price',json={'revision':doc['revision'],'platform_sku':sku})


def test_confirmation_clears_only_missing_price_and_allows_actual_submit(amazon):
    c,catalog,_,_,b,base,remote=amazon
    doc=c.get(base).json();sku=doc['rows'][0]['platform_sku'];other=doc['rows'][1]['platform_sku']
    assert any(e.get('code')=='source_price_review' for e in doc['validation']['rows'][sku]['errors'])
    response=confirm(c,base,doc,sku);assert response.status_code==200,response.text
    verified=response.json()
    assert verified['source_price_reviews'][sku]['price']==102.05
    assert other not in verified['source_price_reviews']
    assert not verified['validation']['current']
    assert confirm(c,base,verified,sku).json()['revision']==verified['revision']
    checked=c.post(base+'/validate',json={'revision':verified['revision']}).json()
    assert checked['validation']['rows'][sku]['valid'],checked['validation']
    assert not checked['validation']['rows'][other]['valid']
    response=c.post(base+'/submit',json=submission(checked,skus=[sku]))
    assert response.status_code==200,response.text
    assert len(remote.posts)==1
    assert confirm(c,base,c.get(base).json(),sku).status_code==409
    with catalog.connect() as db:
        records=db.execute("SELECT details_json FROM walmart_listing_events WHERE batch_id=? AND action='verify_source_price'",(b['id'],)).fetchall()
        assert len(records)==1 and json.loads(records[0][0])['source_sku']==doc['rows'][0]['source_sku']


def test_stale_invalid_price_and_other_errors_are_not_bypassed(amazon):
    c,_,_,_,_,base,_=amazon
    doc=c.get(base).json();sku=doc['rows'][0]['platform_sku']
    assert confirm(c,base,doc | {'revision':doc['revision']-1},sku).status_code==409
    assert confirm(c,base,doc,'other-store-sku').status_code==404
    doc['rows'][0]['orderable'].pop('price')
    doc=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).json()
    assert confirm(c,base,doc,sku).status_code==422
    doc['rows'][0]['orderable']['price']=102.05
    doc['rows'][0]['fulfillment_center_id']='wrong-node'
    doc=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).json()
    doc=confirm(c,base,doc,sku).json()
    checked=c.post(base+'/validate',json={'revision':doc['revision']}).json()
    assert not checked['validation']['rows'][sku]['valid']
    assert any('发货仓' in e['message'] for e in checked['validation']['rows'][sku]['errors'])


def test_price_changes_source_refresh_and_other_batches_need_new_confirmation(amazon):
    c,catalog,_,_,b,base,_=amazon
    doc=c.get(base).json();sku=doc['rows'][0]['platform_sku']
    doc=confirm(c,base,doc,sku).json()
    doc['rows'][0]['orderable']['price']=105
    changed=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).json()
    assert not changed['source_price_reviews']
    changed=confirm(c,base,changed,sku).json()
    refreshed=c.post(base+'/refresh-source',json={'revision':changed['revision']}).json()
    assert not refreshed['source_price_reviews']
    assert 'price' not in refreshed['rows'][0]['orderable']


def test_only_missing_price_is_waived_not_stock_or_identity():
    variant=dict(sku='one',platform_sku='A',price=None,error=MISSING,stock='0')
    other=variant | {'sku':'two','platform_sku':'B'}
    item=dict(source='Amazon',variants=[variant,other],observed_at='2026-11-01T00:00:00Z',
              errors=[f'one：{MISSING}',f'two：{MISSING}','来源 ASIN 与价格证据不一致'])
    errors=source_issues(item,variant,True)
    assert len(errors)==2 and any('库存' in e for e in errors) and any('ASIN' in e for e in errors)


def test_viewer_cannot_confirm(team):
    client,_=login(team,'viewer')
    assert client.post('/api/prelisting/batches/any/walmart/verify-source-price',json={'revision':1,'platform_sku':'one'}).status_code==403
