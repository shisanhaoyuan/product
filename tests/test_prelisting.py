import json
import uuid
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.db import Catalog
from catalog.models import ImportBundle, Listing
from test_auth import team, login, ORIGIN


def product(index=0, **changes):
    asin = f'B{index:09d}'
    check = dict(sku=f'child-{index}', status='verified', landed_price='43.99', existing_price='50.00',
                 selected_basis='50.00', actual='57.99', expected='57.99', multiplier='1.15')
    child = dict(sku=f'child-{index}', asin=asin, seller='Seller A', price='57.99', stock='10',
                 price_check=check, source_facts={'asin': asin, 'currency': 'USD'})
    data = dict(external_id=f'item-{index}',asin=asin,sku=f'parent-{index}',store='MYZ-L-21',brand='Old',
                template=f'template-{index}',observed_at='2026-11-04T10:00:00+00:00',uploaded_at='2026-11-04',
                evidence_kind='persisted_readback',fill_complete=True,save_succeeded=True,readback_verified=True,
                seller='Seller A',copy={'title':'Old chair'},variants=[child])
    return Listing.model_validate(data | changes)


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.delenv('CATALOG_AUTH_ENABLED', raising=False)
    app = create_app(tmp_path / 'plans.db')
    app.state.catalog.ingest(ImportBundle(namespace='test',label='test',listings=[product(i) for i in range(3)]))
    return TestClient(app), app.state.catalog


def inputs(client, **changes):
    store = next(s for s in client.get('/api/listing-stores').json() if s['name'] == 'MYZ-L-17')
    return dict(filters={},store_id=store['id'],brand='NewBrand',multiplier='1.2') | changes


def create(client, settings):
    preview = client.post('/api/prelisting/preview', json=settings)
    assert preview.status_code == 200, preview.text
    payload = settings | {'preview_hash':preview.json()['preview_hash'],'request_key':str(uuid.uuid4())}
    response = client.post('/api/prelisting/batches', json=payload)
    assert response.status_code == 201, response.text
    return response.json(), payload


def test_batch_prices_sku_and_idempotency(local):
    client,catalog = local
    before = catalog.export()
    settings = inputs(client)
    plan = client.post('/api/prelisting/preview',json=settings).json()
    assert plan['total'] == 3 and plan['variant_count'] == 3 and plan['ready_count'] == 3
    for item in plan['items']:
        assert item['variants'][0]['sku'] == 'child-' + item['sku'].split('-')[1]
        assert item['variants'][0]['base'] == '50.00'
        assert item['variants'][0]['price'] == '60.99'  # Never 57.99 * 1.2
        assert item['msrp'] == '121.98'
    assert client.post('/api/prelisting/preview',json=settings).json() == plan
    batch,payload = create(client,settings)
    assert batch['status'] == 'draft' and batch['execution_enabled'] is False
    assert client.post('/api/prelisting/batches',json=payload).json()['id'] == batch['id']
    assert client.post('/api/prelisting/batches',json=payload | {'brand':'changed'}).status_code == 409
    assert client.get('/api/prelisting/batches').json()['total'] == 1
    confirmed = client.post(f"/api/prelisting/batches/{batch['id']}/action",json={'action':'confirm'})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['status'] == 'confirmed' and confirmed.json()['execution_enabled'] is False
    assert client.post(f"/api/prelisting/batches/{batch['id']}/action",json={'action':'confirm'}).status_code == 200
    assert client.post('/api/prelisting/preview',json=settings).json()['can_create'] is False
    assert catalog.export() == before
    reopened = TestClient(create_app(catalog.path))
    assert reopened.get(f"/api/prelisting/batches/{batch['id']}").json()['status'] == 'confirmed'
    assert client.post(f"/api/prelisting/batches/{batch['id']}/action",json={'action':'cancel'}).status_code == 200
    assert client.post('/api/prelisting/preview',json=settings).json()['can_create'] is True
    assert client.post(f"/api/prelisting/batches/{batch['id']}/action",json={'action':'confirm'}).status_code == 409
    with catalog.connect() as db:
        frozen = db.execute('SELECT source_json FROM prelisting_items WHERE batch_id=?',(batch['id'],)).fetchall()
        assert len(frozen) == 3 and all(json.loads(r[0])['brand'] == 'Old' for r in frozen)
        assert db.execute('SELECT COUNT(*) FROM prelisting_events').fetchone()[0] == 3


def test_scopes_filters_and_no_silent_truncation(local):
    client,catalog=local
    catalog.ingest(ImportBundle(namespace='test',label='pages',listings=[product(i) for i in range(3,31)]))
    settings=inputs(client)
    preview=client.post('/api/prelisting/preview',json=settings).json()
    assert preview['total'] == 31  # Cross-page selection, not just 24 on first page.
    filtered=client.post('/api/prelisting/preview',json=settings | {'filters':{'q':'parent-2'}}).json()
    assert filtered['total'] == client.get('/api/products?q=parent-2').json()['total'] == 11
    ids=[item['listing_id'] for item in preview['items'][:2]]
    selected=inputs(client); selected.pop('filters'); selected['listing_ids']=ids
    assert client.post('/api/prelisting/preview',json=selected).json()['total'] == 2
    for bad in [selected | {'listing_ids':[ids[0],ids[0]]}, selected | {'filters':{}}, settings | {'filters':{'unknown':'x'}}]:
        assert client.post('/api/prelisting/preview',json=bad).status_code == 422
    assert client.post('/api/prelisting/preview',json=selected | {'listing_ids':['missing']}).status_code == 409
    assert client.post('/api/prelisting/preview',json=settings | {'filters':{'q':'no-results'}}).status_code == 422
    catalog.ingest(ImportBundle(namespace='test',label='many',listings=[product(i) for i in range(31,501)]))
    assert client.post('/api/prelisting/preview',json=settings).status_code == 422


@pytest.mark.parametrize('change', ['blacklist', 'source', 'store'])
def test_stale_preview_and_confirmation_rejected(local,change):
    client,catalog=local
    settings=inputs(client)
    batch,_=create(client,settings)
    client.post(f"/api/prelisting/batches/{batch['id']}/action",json={'action':'cancel'})
    preview=client.post('/api/prelisting/preview',json=settings).json()
    new_batch,_=create(client,settings)
    if change == 'blacklist':
        client.post('/api/seller-blacklist',json={'source':'Amazon','seller':'Seller A','reason':'缺货'})
    elif change == 'source':
        updated=product(0,observed_at='2026-11-04T11:00:00+00:00',copy={'title':'new record'})
        catalog.ingest(ImportBundle(namespace='test',label='update',listings=[updated]))
    else:
        client.patch('/api/listing-stores/'+str(settings['store_id']),json={'active':False})
    response=client.post(f"/api/prelisting/batches/{new_batch['id']}/action",json={'action':'confirm'})
    assert response.status_code == 409
    assert client.get(f"/api/prelisting/batches/{new_batch['id']}").json()['status'] == 'draft'
    assert client.post('/api/prelisting/batches',json=settings | {'preview_hash':preview['preview_hash'],'request_key':str(uuid.uuid4())}).status_code == 409


def test_review_items_stored_but_not_confirmed(local):
    client,catalog=local
    broken=product(0,observed_at='2026-11-04T11:00:00+00:00')
    broken.variants[0].price_check={}
    broken.variants[0].stock=None
    catalog.ingest(ImportBundle(namespace='test',label='broken',listings=[broken]))
    client.post('/api/seller-blacklist',json={'source':'Amazon','seller':'Seller A','reason':'用户原因'})
    batch,_=create(client,inputs(client))
    assert batch['preview']['ready_count'] == 0
    errors=[e for i in batch['preview']['items'] for e in i['errors']]
    assert any('用户原因' in e for e in errors) and any('库存缺失' in e for e in errors)
    assert client.post(f"/api/prelisting/batches/{batch['id']}/action",json={'action':'confirm'}).status_code == 409


def test_single_rounding_and_multiple_msrp(local):
    client,catalog=local
    single=product(4,variants=[],price='50.59',stock='0',source_facts={'asin':'B000000004','currency':'USD'},
                   checks={'pricing_checks':[dict(field='dc_price',status='verified',landed_price='43.99',existing_price='40',selected_basis='43.99',actual='50.59')]})
    multiple=product(5)
    second=deepcopy(multiple.variants[0]); second.sku='second'; second.asin='B000000009'; second.source_facts['asin']=second.asin
    second.price_check.update(sku='second',landed_price='90',selected_basis='90')
    multiple.variants.append(second)
    catalog.ingest(ImportBundle(namespace='test',label='more',listings=[single,multiple]))
    for q,price,msrp in [('parent-4','48.39','96.78'),('parent-5','55.99','199.98')]:
        result=client.post('/api/prelisting/preview',json=inputs(client,filters={'q':q},multiplier='1.1')).json()['items'][0]
        assert result['variants'][0]['price'] == price
        assert result['msrp'] == msrp


def test_duplicate_asin_same_target_and_other_target(local):
    client,catalog=local
    copied=product(0,external_id='copy',store='Other',template='other')
    catalog.ingest(ImportBundle(namespace='test',label='copy',listings=[copied]))
    assert not client.post('/api/prelisting/preview',json=inputs(client)).json()['can_create']
    settings=inputs(client,filters={'store':'MYZ-L-21'})
    create(client,settings)
    other=next(s for s in client.get('/api/listing-stores').json() if s['name']=='MYZ-L-18')
    assert client.post('/api/prelisting/preview',json=settings | {'store_id':other['id']}).json()['can_create']


@pytest.mark.parametrize('multiplier', ['0','-1','NaN','Infinity','101','1.12345'])
def test_invalid_parameters(local,multiplier):
    client,_=local
    assert client.post('/api/prelisting/preview',json=inputs(client,multiplier=multiplier)).status_code == 422


def test_store_management(local):
    client,catalog=local
    stores=client.get('/api/listing-stores').json()
    assert next(s for s in stores if s['name']=='MY-L-16')['marketplace_name'] == 'HomeYao'
    assert next(s for s in stores if s['name']=='MYZ-L-14')['marketplace_name'] == 'YQ1'
    assert not any(s['name']=='MYZ-L-999' for s in stores)
    entry=client.post('/api/listing-stores',json={'name':'MYZ-L-999','marketplace_name':'Demo'}).json()
    assert client.post('/api/listing-stores',json={'name':' myz-l-999 '}).status_code == 409
    assert client.patch(f"/api/listing-stores/{entry['id']}",json={'active':False}).status_code == 200
    Catalog(catalog.path)
    assert not next(s for s in client.get('/api/listing-stores').json() if s['id']==entry['id'])['active']
    assert client.post('/api/prelisting/preview',json=inputs(client,store_id=entry['id'])).status_code == 409


@pytest.mark.parametrize('role', ['admin','editor','viewer'])
def test_permissions(team,role):
    team.state.catalog.ingest(ImportBundle(namespace='test',label='test',listings=[product()]))
    client,_=login(team,role)
    settings=inputs(client)
    assert TestClient(team,base_url=ORIGIN).get('/api/prelisting/batches').status_code == 401
    assert client.get('/api/prelisting/batches').status_code == 200
    if role == 'viewer':
        for path,body in [('/api/prelisting/preview',settings),('/api/prelisting/batches',{}),('/api/prelisting/batches/x/action',{'action':'confirm'}),('/api/listing-stores',{'name':'test'})]:
            assert client.post(path,json=body).status_code == 403
        assert client.patch('/api/listing-stores/1',json={'active':False}).status_code == 403
    else:
        batch,_=create(client,settings)
        assert client.post(f"/api/prelisting/batches/{batch['id']}/action",json={'action':'confirm'}).status_code == 200
        client.headers.pop('X-CSRF-Token')
        assert client.post('/api/prelisting/preview',json=settings).status_code == 403


@pytest.mark.parametrize('fault', ['sku', 'asin', 'shipping', 'malformed', 'actual', 'ambiguous'])
def test_conflicting_price_evidence_never_uses_current_price(local, fault):
    client,catalog=local
    changed=product(0,observed_at='2026-11-04T12:00:00+00:00')
    child=changed.variants[0]
    if fault == 'sku': child.price_check['sku']='another-sku'
    if fault == 'asin': child.source_facts['asin']='B999999999'
    if fault == 'shipping': child.source_facts['frozen_price_basis']={'source_price':'40','shipping_price':'8'}
    if fault == 'malformed': child.price_check['price_reference']='invalid'
    if fault == 'actual': child.price='100'
    if fault == 'ambiguous': child.source_facts['offer_ambiguous']=True
    catalog.ingest(ImportBundle(namespace='test',label='changed',listings=[changed]))
    preview=client.post('/api/prelisting/preview',json=inputs(client,filters={'q':'parent-0'}))
    assert preview.status_code == 200
    row=preview.json()['items'][0]
    assert row['status']=='review' and row['variants'][0]['price'] is None


def test_concurrent_retry_creates_one_batch(local):
    from concurrent.futures import ThreadPoolExecutor
    client,catalog=local
    settings=inputs(client)
    preview=client.post('/api/prelisting/preview',json=settings).json()
    payload=settings | {'preview_hash':preview['preview_hash'],'request_key':str(uuid.uuid4())}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _: client.post('/api/prelisting/batches',json=payload), range(2)))
    assert [r.status_code for r in responses]==[201,201]
    assert responses[0].json()['id']==responses[1].json()['id']
    assert client.get('/api/prelisting/batches').json()['total']==1
    with catalog.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM prelisting_claims').fetchone()[0] == 3
