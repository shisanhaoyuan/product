import json
import uuid

import pytest

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


def publish(env):
    c, _, _, _, _, base, _ = env
    attempt = send(env, ready(env))
    response = c.post(base + f"/attempts/{attempt['id']}/refresh")
    assert response.status_code == 200, response.text
    assert all(r['published_status'] == 'PUBLISHED' for r in response.json()['results'].values())
    return attempt


def test_giga_price_filters_combine_store_search_export_and_selection(publishing):
    c,catalog,_,_,b,_,_=publishing
    publish(publishing)
    sid=str(b['preview']['store']['id'])
    # Publishing fixture refreshes W2's UPC, producing two equal price observations.
    assert ids(c,price_change='unknown')=={'W1'}
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


def test_confirmed_publication_survives_local_batch_cancellation(publishing):
    c,catalog,_,_,b,_,_=publishing
    publish(publishing)
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


def test_multi_filters_or_within_group_and_between_groups(publishing):
    c,catalog,_,_,b,_,_=publishing
    publish(publishing)
    sid=str(b['preview']['store']['id'])
    new_prices(catalog,{'W1':80,'W2':60})
    multi=json.dumps({'price_change':['up','down'],'listed_store':[sid,'999999']})
    assert ids(c,multi=multi)=={'W1','W2'}
    assert ids(c,multi=multi,q='W1')=={'W1'}
    page=c.get('/api/products',params={'multi':multi,'page_size':1}).json()
    assert page['total']==2 and len(page['items'])==1
    second=c.get('/api/products',params={'multi':multi,'page_size':1,'page':2}).json()
    assert page['items'][0]['id']!=second['items'][0]['id']
    csv=c.get('/api/export.csv',params={'multi':multi,'q':'W1'}).text
    assert 'W1' in csv and 'W2' not in csv
    data=PlanInput.model_validate(plan(c) | {'filters':{'multi':multi,'q':'W1'}})
    with catalog.connect() as db:
        assert [r['sku'] for r in selected_rows(catalog,db,data)]==['W1']
    assert ids(c,multi=json.dumps({'price_change':['down'],'listed_store':[sid]}))=={'W2'}
    assert ids(c,multi=json.dumps({'price_change':['up','down'],'listed_store':['999999']}))==set()
    date=catalog.export()[0]['uploaded_at'][:10]
    assert ids(c,multi=json.dumps({'date':[date,'1999-01-01']}))=={'W1','W2'}
    for invalid in ({'source':['Amazon']},{'date':'today'},{'price_change':['invalid']},{'date':['x']*51}):
        assert c.get('/api/products',params={'multi':json.dumps(invalid)}).status_code==422
        assert c.get('/api/export.csv',params={'multi':json.dumps(invalid)}).status_code==422


def test_store_filter_excludes_drafts_submission_and_requires_matching_api_feedback(publishing):
    c, catalog, _, _, batch, base, remote = publishing
    sid = str(batch['preview']['store']['id'])
    assert ids(c, listed_store=sid) == set()  # Prepared local draft.
    doc = ready(publishing)
    assert ids(c, listed_store=sid) == set()  # Local validation is not publication.
    attempt = send(publishing, doc)
    assert ids(c, listed_store=sid) == set()  # feedId alone is not publication.
    first = doc['rows'][0]
    other = doc['rows'][1]
    remote.item_status[other['platform_sku']] = 'DATA_ERROR'
    response = c.post(base + f"/attempts/{attempt['id']}/refresh")
    assert response.status_code == 200, response.text
    assert ids(c, listed_store=sid) == {first['source_sku']}
    exported = c.get('/api/export.csv', params={'listed_store': sid}).text
    assert first['source_sku'] in exported and other['source_sku'] not in exported
    query = PlanInput.model_validate(plan(c) | {'filters': {'listed_store': sid}})
    with catalog.connect() as db:
        assert {r['sku'] for r in selected_rows(catalog, db, query)} == {first['source_sku']}
    remote.item_override = 'WRONG-SKU'
    response = c.post(base + f"/attempts/{attempt['id']}/refresh")
    assert response.status_code == 200, response.text
    assert ids(c, listed_store=sid) == set()  # New failed readback supersedes success.


@pytest.mark.parametrize('status,published_status', [
    ('processing', ''), ('sending', ''), ('unknown', ''), ('failed', ''),
    ('rejected', ''), ('unpublished', 'UNPUBLISHED'), ('stock_pending', ''),
    ('ingested', ''), ('success', ''), ('published', ''), ('published', 'UNPUBLISHED'),
])
def test_store_filter_rejects_nonpublication_results(publishing, status, published_status):
    c, catalog, _, _, batch, _, _ = publishing
    attempt = publish(publishing)
    with catalog.connect() as db:
        row = db.execute('SELECT results_json FROM walmart_listing_attempts WHERE id=?', (attempt['id'],)).fetchone()
        outcomes = json.loads(row[0])
        for outcome in outcomes.values():
            outcome.update(status=status, published_status=published_status)
        db.execute('UPDATE walmart_listing_attempts SET results_json=? WHERE id=?', (json.dumps(outcomes), attempt['id']))
    assert ids(c, listed_store=str(batch['preview']['store']['id'])) == set()


@pytest.mark.parametrize('change', ['new_claim', 'other_partner', 'mismatched_row', 'missing_feed'])
def test_old_or_mismatched_publication_does_not_match_store(publishing, change):
    c, catalog, _, _, batch, _, _ = publishing
    attempt = publish(publishing)
    sid = str(batch['preview']['store']['id'])
    with catalog.connect() as db:
        if change == 'other_partner':
            db.execute("UPDATE walmart_accounts SET partner_id='different' WHERE store_id=?", (int(sid),))
        elif change == 'mismatched_row':
            row = db.execute('SELECT rows_json FROM walmart_listing_attempts WHERE id=?', (attempt['id'],)).fetchone()
            submitted = json.loads(row[0])
            for value in submitted: value['platform_sku'] = 'other'
            db.execute('UPDATE walmart_listing_attempts SET rows_json=? WHERE id=?', (json.dumps(submitted), attempt['id']))
        elif change == 'missing_feed':
            db.execute("UPDATE walmart_listing_attempts SET feed_id='' WHERE id=?", (attempt['id'],))
        else:
            old = dict(db.execute('SELECT * FROM walmart_listing_attempts WHERE id=?', (attempt['id'],)).fetchone())
            old.update(id=uuid.uuid4().hex, request_key=uuid.uuid4().hex, feed_id='new-feed', state='processing', results_json='{}')
            db.execute('INSERT INTO walmart_listing_attempts (' + ','.join(old) + ') VALUES (' + ','.join('?' for _ in old) + ')', list(old.values()))
            db.execute('UPDATE walmart_listing_claims SET attempt_id=? WHERE attempt_id=?', (old['id'], attempt['id']))
    assert ids(c, listed_store=sid) == set()


def test_draft_in_second_store_does_not_inherit_first_store_publication(publishing):
    c, _, _, _, batch, _, _ = publishing
    publish(publishing)
    first_store = str(batch['preview']['store']['id'])
    second_store = next(s for s in c.get('/api/listing-stores').json() if s['name'] == 'MYZ-L-18')
    create(c, plan(c, store_id=second_store['id']))
    assert ids(c, listed_store=first_store) == {'W1', 'W2'}
    assert ids(c, listed_store=str(second_store['id'])) == set()
    assert ids(c, multi=json.dumps({'listed_store': [first_store, str(second_store['id'])]})) == {'W1', 'W2'}


def test_new_unpublished_api_response_removes_previous_match(publishing):
    import httpx
    c, _, _, _, batch, base, remote = publishing
    attempt = publish(publishing)
    sid = str(batch['preview']['store']['id'])
    assert ids(c, listed_store=sid) == {'W1', 'W2'}
    original = remote.handler
    def handler(request):
        if request.url.path.startswith('/v3/items/'):
            return httpx.Response(200, json={'ItemResponse': [{
                'sku': request.url.path.rsplit('/', 1)[-1], 'publishedStatus': 'UNPUBLISHED',
                'unpublishedReasons': {'reason': ['Not currently published']}}]})
        return original(request)
    from catalog.walmart_publishing_client import WalmartPublishingClient
    c.app.state.walmart_publisher = WalmartPublishingClient(httpx.MockTransport(handler))
    response = c.post(base + f"/attempts/{attempt['id']}/refresh")
    assert response.status_code == 200, response.text
    assert ids(c, listed_store=sid) == set()
