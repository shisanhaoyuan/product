import copy
import json
import uuid

import pytest

from catalog.prelisting import encoded
from catalog.app import create_app
from fastapi.testclient import TestClient
from test_prelisting import create, local, product, inputs
from catalog.models import ImportBundle
from test_giga import settings
from test_giga_prelisting import warehouse
from test_walmart_accounts import vault
from test_walmart_publishing import publishing, ready, send, PT


def target(env, **changes):
    c,_,_,_,b,_,_=env
    store=next(s for s in c.get('/api/listing-stores').json() if s['name']=='MYZ-L-18')
    return dict(listing_ids=[i['listing_id'] for i in b['preview']['items']],
        store_id=store['id'],brand='TargetBrand',multiplier='1.05',
        template_batches={i['listing_id']:b['id'] for i in b['preview']['items']}) | changes


def test_reuse_cross_store_content_brand_identity_and_independent_persistence(publishing, monkeypatch):
    c,catalog,_,_,b,base,remote=publishing
    doc=ready(publishing)
    for row in doc['rows']:
        row['visible'].update(productName='NewBrand Solid Chair '+row['source_sku'],
            shortDescription='<p>NewBrand chair, 24.5 inches</p>',keyFeatures=['NewBrand wood frame','Oakwood finish'])
        row['orderable']['msrp']=99.99
    saved=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).json()
    export=catalog.export();calls=len(remote.calls)
    # Reuse is a local copy, even with AI configured; creation must not start a job.
    monkeypatch.setattr('catalog.listing_generation_jobs.auto_start',lambda *a:pytest.fail('unexpected AI job'))
    inputs=target(publishing)
    options=c.post('/api/prelisting/templates',json=inputs)
    assert options.status_code==200,options.text
    assert len(options.json()['items'])==2
    assert all(i['options'][0]['batch_id']==b['id'] for i in options.json()['items'])
    new,payload=create(c,inputs)
    again=c.post('/api/prelisting/batches',json=payload)
    assert again.json()['id']==new['id']
    newbase=f"/api/prelisting/batches/{new['id']}/walmart"
    clone=c.get(newbase).json()
    assert clone['validation']['current'] is False and not clone['attempts'] and not clone['results']
    assert clone['brand']=='TargetBrand' and clone['store']['id']==inputs['store_id']
    assert len(clone['rows'])==2
    for old,row in zip(saved['rows'],clone['rows']):
        assert row['source_sku']==old['source_sku'] and row['listing_id']==old['listing_id']
        assert row['platform_sku'].startswith('MY18') and row['platform_sku']!=old['platform_sku']
        assert row['visible']['brand']=='TargetBrand'
        assert row['visible']['productName']=='TargetBrand Solid Chair '+row['source_sku']
        assert row['visible']['shortDescription']=='<p>TargetBrand chair, 24.5 inches</p>'
        assert row['visible']['keyFeatures']==['TargetBrand wood frame','Oakwood finish']
        assert row['visible']['mainImageUrl']==old['visible']['mainImageUrl']
        assert row['visible']['assembledProductHeight']==old['visible']['assembledProductHeight']
        assert row['product_type']==PT and row['fulfillment_center_id']==''
        assert row['identification']==dict(mode='identifier',type='EAN',value='',submission_id='')
        assert row['orderable']['price']==72.80  # 69.33 source basis * 1.05, not the old 79.73.
        assert row['orderable']['msrp']==99.99 and row['orderable']['ShippingWeight']==10
    assert len(remote.calls)==calls
    clone['rows'][0]['visible']['shortDescription']='Edited target only'
    assert c.patch(newbase,json={k:clone[k] for k in ('revision','version','rows')}).status_code==200
    assert c.get(base).json()['rows']==saved['rows'] and catalog.export()==export
    reopened=TestClient(create_app(catalog.path))
    assert reopened.get(newbase).json()['rows'][0]['visible']['shortDescription']=='Edited target only'
    with catalog.connect() as db:
        assert db.execute("SELECT count(*) FROM walmart_listing_events WHERE batch_id=? AND action='reuse_template'",(new['id'],)).fetchone()[0]==1
    # Frozen clone survives source-template edits and cancellation without tracking it live.
    saved['rows'][0]['visible']['productName']='Other title'
    c.patch(base,json={k:saved[k] for k in ('revision','version','rows')})
    c.post(f"/api/prelisting/batches/{b['id']}/action",json={'action':'cancel'})
    assert c.post(f"/api/prelisting/batches/{new['id']}/action",json={'action':'confirm'}).status_code==200


def test_template_change_between_preview_and_create_is_rejected(publishing):
    c,catalog,_,_,b,base,_=publishing
    doc=ready(publishing);inputs=target(publishing)
    p=c.post('/api/prelisting/preview',json=inputs).json()
    doc['rows'][0]['visible']['shortDescription']='Changed after preview'
    c.patch(base,json={k:doc[k] for k in ('revision','version','rows')})
    response=c.post('/api/prelisting/batches',json=inputs|dict(preview_hash=p['preview_hash'],request_key=str(uuid.uuid4())))
    assert response.status_code==409
    with catalog.connect() as db:
        assert db.execute('SELECT count(*) FROM prelisting_batches').fetchone()[0]==1
        assert not db.execute('SELECT 1 FROM platform_skus WHERE store_id=?',(inputs['store_id'],)).fetchone()


def test_wrong_product_missing_sku_trash_and_same_store_are_rejected(publishing):
    c,catalog,_,_,b,base,_=publishing
    ready(publishing);inputs=target(publishing)
    assert not c.post('/api/prelisting/preview',json=inputs|{'store_id':b['preview']['store']['id']}).json()['can_create']
    bad=inputs|{'template_batches':{'not-selected':b['id']}}
    assert c.post('/api/prelisting/preview',json=bad).status_code==409
    with catalog.connect() as db:
        source=db.execute('SELECT rows_json FROM walmart_listing_documents WHERE batch_id=?',(b['id'],)).fetchone()
        rows=json.loads(source[0]);rows[0]['source_sku']='other-sku'
        db.execute('UPDATE walmart_listing_documents SET rows_json=? WHERE batch_id=?',(encoded(rows),b['id']))
    assert c.post('/api/prelisting/preview',json=inputs).status_code==409
    opts=c.post('/api/prelisting/templates',json=inputs).json()
    assert sum(bool(i['options']) for i in opts['items'])==1
    c.post(f"/api/prelisting/batches/{b['id']}/action",json={'action':'cancel'})
    c.post('/api/prelisting/batches/bulk-action',json={'ids':[b['id']],'action':'delete'})
    assert c.post('/api/prelisting/preview',json=inputs).status_code==409
    assert all(not i['options'] for i in c.post('/api/prelisting/templates',json=inputs).json()['items'])


def test_published_template_reusable_without_copying_submission_or_exemption(publishing):
    c,_,_,_,b,base,_=publishing
    doc=ready(publishing)
    doc['rows'][0]['identification']=dict(mode='exemption',type='EAN',value='',submission_id='approved-123')
    c.patch(base,json={k:doc[k] for k in ('revision','version','rows')})
    doc=c.post(base+'/validate',json={'revision':doc['revision']+1}).json()
    send(publishing,doc)
    inputs=target(publishing)
    new,_=create(c,inputs)
    clone=c.get(f"/api/prelisting/batches/{new['id']}/walmart").json()
    assert clone['rows'][0]['identification']['mode']=='identifier'
    assert not clone['attempts'] and not clone['results'] and not clone['validation'].get('id')


def test_asin_identity_change_cannot_use_old_template(publishing):
    c,catalog,_,_,b,_,_=publishing
    ready(publishing)
    with catalog.connect() as db:
        record=db.execute('SELECT basis_json FROM walmart_listing_documents WHERE batch_id=?',(b['id'],)).fetchone()
        basis=json.loads(record[0]);basis['items'][0]['variants'][0]['asin']='B000000001'
        db.execute('UPDATE walmart_listing_documents SET basis_json=? WHERE batch_id=?',(encoded(basis),b['id']))
    assert c.post('/api/prelisting/preview',json=target(publishing)).status_code==409


def test_target_store_warehouse_and_brand_identifier_pool_are_used(publishing):
    c,catalog,_,_,b,base,_=publishing
    ready(publishing)
    from test_walmart_accounts import Remote,bind
    from test_listing_preparation import code
    inputs=target(publishing)
    account_remote=Remote().install(c.app);account_remote.partner='target-18'
    bind(c,inputs['store_id'])
    with catalog.connect() as db:
        db.execute('INSERT INTO listing_store_preparation VALUES(?,?,?,?,?)',(inputs['store_id'],'target-18',1,
            encoded(dict(default_brand='TargetBrand',fulfillment_center_id='target-node-18',exemptions=[])),'2026-11-17T00:00:00+00:00'))
    assert c.post('/api/walmart/identifiers',json={'items':[
        code(brand='TargetBrand'),code('4006381333931',brand='TargetBrand')]}).status_code==200
    new,payload=create(c,inputs)
    rows=c.get(f"/api/prelisting/batches/{new['id']}/walmart").json()['rows']
    assert {r['identification']['value'] for r in rows}=={'9780306406157','4006381333931'}
    assert all(r['fulfillment_center_id']=='target-node-18' for r in rows)
    assert c.post('/api/prelisting/batches',json=payload).json()['id']==new['id']
    with catalog.connect() as db:
        assert db.execute("SELECT count(*) FROM listing_identifier_events WHERE action='assign'").fetchone()[0]==2
    # Source template barcodes and warehouse remain unchanged.
    assert all(r['fulfillment_center_id']=='node-17' for r in c.get(base).json()['rows'])


def test_partial_batch_does_not_copy_template_into_unselected_product(publishing):
    c,_,_,_,b,_,_=publishing
    ready(publishing)
    inputs=target(publishing)
    lid=b['preview']['items'][0]['listing_id']
    new,_=create(c,inputs|{'template_batches':{lid:b['id']}})
    doc=c.get(f"/api/prelisting/batches/{new['id']}/walmart").json()
    assert len(doc['rows'])==2
    for row in doc['rows']:
        assert bool(row['product_type'])==(row['listing_id']==lid)


def test_multi_variant_reversed_rows_subset_and_partial_coverage(local):
    c,catalog=local
    data=product(0).model_dump(mode='json')
    data['observed_at']='2026-11-04T11:00:00+00:00'
    child=data['variants'][0]
    second=copy.deepcopy(child);second.update(sku='child-black',asin='B999999999')
    second['price_check']['sku']='child-black'
    second['source_facts']['asin']='B999999999'
    data['variants'].append(second)
    from catalog.models import Listing
    catalog.ingest(ImportBundle(namespace='test',label='variants',listings=[Listing.model_validate(data)]))
    selection=inputs(c,filters={'q':'parent-0'})
    b,_=create(c,selection);base=f"/api/prelisting/batches/{b['id']}/walmart"
    doc=c.post(base+'/prepare').json()
    for row in doc['rows']:
        row['product_type']=PT
        row['visible'].update(productName='NewBrand '+row['source_sku'],color=row['source_sku'],shortDescription='Description '+row['source_sku'])
    doc['rows'].reverse()
    assert c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).status_code==200
    targetstore=next(s for s in c.get('/api/listing-stores').json() if s['name']=='MYZ-L-18')
    lid=b['preview']['items'][0]['listing_id']
    newinput=dict(listing_ids=[lid],store_id=targetstore['id'],brand='Next',multiplier='1.15',template_batches={lid:b['id']})
    cloned,_=create(c,newinput)
    newdoc=c.get(f"/api/prelisting/batches/{cloned['id']}/walmart").json()
    assert {r['source_sku']:r['visible']['color'] for r in newdoc['rows']}=={'child-0':'child-0','child-black':'child-black'}
    # A subset reuses only its own variant, irrespective of row order.
    third=next(s for s in c.get('/api/listing-stores').json() if s['name']=='MYZ-L-19')
    subset=newinput|dict(store_id=third['id'],variant_skus={lid:['child-black']})
    b3,_=create(c,subset)
    rows=c.get(f"/api/prelisting/batches/{b3['id']}/walmart").json()['rows']
    assert len(rows)==1 and rows[0]['source_sku']=='child-black' and rows[0]['visible']['color']=='child-black'
    # A partial saved template must never silently populate the other variant.
    other=next(s for s in c.get('/api/listing-stores').json() if s['name']=='MYZ-L-20')
    assert c.post('/api/prelisting/preview',json=newinput|dict(store_id=other['id'],template_batches={lid:b3['id']})).status_code==409
