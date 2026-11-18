import json
import uuid

from catalog.models import ImportBundle
from catalog.prelisting import selected_rows, PlanInput
from test_giga import settings
from test_giga_prelisting import warehouse, plan
from test_prelisting import create
from test_walmart_accounts import vault
from test_walmart_publishing import publishing, ready, send
from test_catalog import listing, bundle
from catalog.app import create_app
from fastapi.testclient import TestClient


def ids(client, **params):
    response = client.get('/api/products', params=params)
    assert response.status_code == 200, response.text
    return {p['sku'] for p in response.json()['items']}


def new_prices(catalog, values):
    # Import a genuine later source snapshot, leaving all identities unchanged.
    rows=[]
    for item in catalog.export():
        data={k:v for k,v in item.items() if k not in ('id','namespace')}
        data['observed_at']='2030-01-01T00:00:00+00:00'
        data['price']=str(values[data['sku']])
        data['source_facts']['price']=data['price']
        rows.append(data)
    catalog.ingest(ImportBundle(namespace=catalog.export()[0]['namespace'],label='later',listings=rows))


def test_giga_price_filters_combine_store_search_export_and_selection(warehouse):
    c,catalog,_,_=warehouse
    b,_=create(c,plan(c))
    sid=str(b['preview']['store']['id'])
    assert ids(c,price_change='unknown')=={'W1','W2'}
    new_prices(catalog,{'W1':80,'W2':60})
    assert ids(c,price_change='up')=={'W1'}
    assert ids(c,price_change='down')=={'W2'}
    assert ids(c,price_change='unchanged')==set()
    assert ids(c,price_change='unknown')==set()
    assert ids(c,price_change='up',listed_store=sid,q='W1')=={'W1'}
    assert ids(c,price_change='up',listed_store=sid,q='W2')==set()
    assert ids(c,price_change='up',listed_store='999999')==set()
    csv=c.get('/api/export.csv',params={'price_change':'up','listed_store':sid}).text
    assert 'W1' in csv and 'W2' not in csv
    data=PlanInput.model_validate(plan(c) | {'filters':{'source':'GigaB2B','listed_store':sid,'price_change':'up'}})
    with catalog.connect() as db:
        selected=selected_rows(catalog,db,data)
    assert [r['sku'] for r in selected]==['W1']
    c.post(f"/api/prelisting/batches/{b['id']}/action",json={'action':'cancel'})
    assert ids(c,listed_store=sid)==set()


def test_actual_submission_keeps_store_history_even_if_batch_cancelled(publishing):
    c,catalog,_,_,b,_,_=publishing
    send(publishing,ready(publishing))
    with catalog.connect() as db:
        db.execute("UPDATE prelisting_batches SET status='cancelled' WHERE id=?",(b['id'],))
    assert ids(c,listed_store=str(b['preview']['store']['id']))=={'W1','W2'}


def test_latest_unchanged_patrol_supersedes_older_rise(warehouse):
    c,catalog,_,_=warehouse
    new_prices(catalog,{'W1':80,'W2':67})
    assert ids(c,price_change='unchanged')=={'W2'}
    lid=next(r['id'] for r in catalog.export() if r['sku']=='W1')
    with catalog.connect() as db:
        db.execute('INSERT INTO giga_patrol_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('run','test','2031-01-01','2031-01-01','complete',1,1,0,0,0,0,''))
        same=json.dumps({'price':'80.00','currency':'USD'})
        db.execute('INSERT INTO giga_patrol_items VALUES(?,?,?,?,?,?,?,?)',
                   ('run',lid,'W1','2031-01-01','ok',same,same,'[]'))
    assert ids(c,price_change='up')==set()
    assert ids(c,price_change='unchanged')=={'W1','W2'}


def test_amazon_compares_same_child_currency_and_never_erp_price(tmp_path):
    app=create_app(tmp_path/'filters.db');c=TestClient(app);catalog=app.state.catalog
    item=listing();item.variants.append(item.variants[0].model_copy(update={'sku':'child-b','asin':'B012345670'}))
    catalog.ingest(bundle([item]));lid=catalog.export()[0]['id']
    def capture(sku,asin,at,price,currency='USD'):
        with catalog.connect() as db:
            db.execute('INSERT INTO source_captures VALUES(?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex,lid,sku,asin,at,None,json.dumps({'basis_price':price,'currency':currency})))
    assert ids(c,price_change='unknown')=={item.sku}
    for sku,asin,prices in [('child-a','B012345679',('20','30')),('child-b','B012345670',('100','80'))]:
        for day,price in enumerate(prices,1):capture(sku,asin,f'2030-01-0{day}',price)
    assert ids(c,price_change='up')==ids(c,price_change='down')=={item.sku}
    assert ids(c,price_change='unchanged')==set()
    capture('child-a','B012345679','2030-01-03','30','EUR')
    assert ids(c,price_change='up')==set()
    assert ids(c,price_change='unknown')=={item.sku}
    capture('child-b','B012345670','2030-01-03','NaN')
    assert ids(c,price_change='down')==set()
    assert c.get('/api/products',params={'price_change':'oops'}).status_code==422


def test_missing_child_history_cannot_be_reported_as_unchanged(tmp_path):
    app=create_app(tmp_path/'missing-child.db');c=TestClient(app);catalog=app.state.catalog
    item=listing();item.variants.append(item.variants[0].model_copy(update={'sku':'missing','asin':'B012345670'}))
    catalog.ingest(bundle([item]));lid=catalog.export()[0]['id']
    with catalog.connect() as db:
        for day in (1,2):
            db.execute('INSERT INTO source_captures VALUES(?,?,?,?,?,?,?)',
                       (str(day),lid,'child-a','B012345679',f'2030-01-0{day}',None,
                        json.dumps({'basis_price':'20','currency':'USD'})))
    assert ids(c,price_change='unchanged')==set()
    assert ids(c,price_change='unknown')=={item.sku}
