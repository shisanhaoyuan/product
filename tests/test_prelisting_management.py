"""Direct batch cleanup preserves audit, identifiers and platform safety."""
from fastapi.testclient import TestClient
import pytest

from catalog.app import create_app
from test_prelisting import local, inputs, create
from test_auth import team, login
from test_walmart_publishing import publishing
from test_giga_prelisting import warehouse
from test_giga import settings
from test_walmart_accounts import vault


def cancelled(client, **changes):
    batch,_=create(client,inputs(client,**changes))
    assert client.post(f"/api/prelisting/batches/{batch['id']}/action",json={'action':'cancel'}).status_code==200
    return batch


def change(client, ids, action='delete'):
    return client.post('/api/prelisting/batches/bulk-action',json={'ids':ids,'action':action})


def test_status_store_brand_search_and_filtered_pagination(local):
    client,catalog=local
    first=cancelled(client,brand='FirstBrand')
    second=cancelled(client,brand='SecondBrand')
    other=next(s for s in client.get('/api/listing-stores').json() if s['name']=='MYZ-L-18')
    draft,_=create(client,inputs(client,store_id=other['id'],brand='AnotherBrand'))
    result=client.get('/api/prelisting/batches',params={'status':'cancelled','page_size':1}).json()
    assert result['total']==2 and len(result['items'])==1
    result=client.get('/api/prelisting/batches',params={'q':'firstbrand','status':'cancelled'}).json()
    assert [item['id'] for item in result['items']]==[first['id']]
    result=client.get('/api/prelisting/batches',params={'store_id':other['id']}).json()
    assert [item['id'] for item in result['items']]==[draft['id']]
    assert client.get('/api/prelisting/batches?status=published').status_code==422


def test_creation_time_filters_use_instants_include_boundaries_and_paginate(local):
    client,catalog=local
    first=cancelled(client,brand='FirstDay')
    middle=cancelled(client,brand='SecondDay')
    last=cancelled(client,brand='EndOfDay')
    with catalog.connect() as db:
        for bid,at in [(first['id'],'2026-11-20T00:00:00+08:00'),
                       (middle['id'],'2026-11-20T16:00:00.123456+00:00'),
                       (last['id'],'2026-11-21T15:59:59.999000+00:00')]:
            db.execute('UPDATE prelisting_batches SET created_at=? WHERE id=?',(at,bid))
    filters={'created_from':'2026-11-21T00:00:00+08:00','created_to':'2026-11-21T23:59:59.999+08:00'}
    result=client.get('/api/prelisting/batches',params=filters).json()
    assert result['total']==2
    assert {row['id'] for row in result['items']}=={middle['id'],last['id']}
    pages=[client.get('/api/prelisting/batches',params=filters|{'page_size':1,'page':page}).json() for page in (1,2)]
    assert all(result['total']==2 and len(result['items'])==1 for result in pages)
    assert len({result['items'][0]['id'] for result in pages})==2
    exact={'created_from':'2026-11-19T16:00:00Z','created_to':'2026-11-19T16:00:00Z'}
    assert [row['id'] for row in client.get('/api/prelisting/batches',params=exact).json()['items']]==[first['id']]
    before=client.get('/api/prelisting/batches',params={'created_to':'2026-11-20T16:00:00Z'}).json()
    assert [row['id'] for row in before['items']]==[first['id']]
    after=client.get('/api/prelisting/batches',params={'created_from':'2026-11-21T15:59:59.999Z'}).json()
    assert [row['id'] for row in after['items']]==[last['id']]
    combined=client.get('/api/prelisting/batches',params=filters|{'status':'cancelled',
        'store_id':last['preview']['store']['id'],'q':'EndOfDay'}).json()
    assert [row['id'] for row in combined['items']]==[last['id']]
    assert change(client,[first['id'],middle['id'],last['id']]).status_code==200
    assert client.get('/api/prelisting/batches',params=filters).json()['total']==0
    assert client.get('/api/prelisting/batches',params=filters|{'trash':'true'}).json()['total']==2


@pytest.mark.parametrize('filters',[
    {'created_from':'2026-11-20T00:00:00'},
    {'created_to':'2026-11-31T00:00:00Z'},
    {'created_from':'2026-11-21T00:00:00Z','created_to':'2026-11-20T00:00:00Z'},
])
def test_creation_time_filter_rejects_invalid_or_ambiguous_ranges(local,filters):
    client,_=local
    response=client.get('/api/prelisting/batches',params=filters)
    assert response.status_code==422,response.text


def test_batch_delete_restore_is_idempotent_persistent_and_preserves_records(local):
    client,catalog=local
    first=cancelled(client);second=cancelled(client)
    before=catalog.export()
    with catalog.connect() as db:
        snapshots=[tuple(row) for row in db.execute('SELECT * FROM prelisting_items ORDER BY batch_id,listing_id')]
        codes=[tuple(row) for row in db.execute('SELECT * FROM platform_skus ORDER BY store_id,source_key,identity_key')]
    ids=[first['id'],second['id']]
    response=change(client,ids);assert response.status_code==200,response.text
    assert response.json()['changed']==2
    assert client.get('/api/prelisting/batches').json()['total']==0
    assert client.get('/api/prelisting/batches?trash=true').json()['total']==2
    assert change(client,ids).json()['changed']==0
    assert client.get('/api/prelisting/batches?trash=true&page_size=1&page=2').json()['total']==2
    detail=client.get(f"/api/prelisting/batches/{ids[0]}").json()
    assert detail['deleted_at'] and detail['status']=='cancelled'
    assert client.post(f"/api/prelisting/batches/{ids[0]}/action",json={'action':'confirm'}).status_code==409
    reopened=TestClient(create_app(catalog.path))
    assert reopened.get('/api/prelisting/batches').json()['total']==0
    assert change(reopened,ids,'restore').json()['changed']==2
    assert change(reopened,ids,'restore').json()['changed']==0
    assert reopened.get('/api/prelisting/batches?trash=true').json()['total']==0
    assert all(item['status']=='cancelled' for item in reopened.get('/api/prelisting/batches').json()['items'])
    assert catalog.export()==before
    with catalog.connect() as db:
        assert snapshots==[tuple(row) for row in db.execute('SELECT * FROM prelisting_items ORDER BY batch_id,listing_id')]
        assert codes==[tuple(row) for row in db.execute('SELECT * FROM platform_skus ORDER BY store_id,source_key,identity_key')]
        assert db.execute('SELECT count(*) FROM prelisting_claims').fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM prelisting_events WHERE action='trash'").fetchone()[0]==2
        assert db.execute("SELECT count(*) FROM prelisting_events WHERE action='restore'").fetchone()[0]==2


def test_direct_delete_accepts_drafts_without_a_separate_cancel(local):
    client,catalog=local
    first=cancelled(client)
    draft,_=create(client,inputs(client))
    result=change(client,[first['id'],draft['id']])
    assert result.status_code==200,result.text
    assert result.json()['changed']==2
    assert client.get('/api/prelisting/batches').json()['total']==0
    assert client.get('/api/prelisting/batches?trash=true').json()['total']==2
    with catalog.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM prelisting_claims').fetchone()[0]==0
    assert client.post('/api/prelisting/preview',json=inputs(client)).json()['can_create']
    assert change(client,[draft['id']],'restore').status_code==200
    detail=client.get(f"/api/prelisting/batches/{draft['id']}").json()
    assert detail['status']=='cancelled' and not detail['deleted_at']


def test_missing_ids_are_atomic_and_duplicates_rejected(local):
    client,catalog=local
    first=cancelled(client);draft,_=create(client,inputs(client))
    assert change(client,[first['id'],'missing']).status_code==404
    assert change(client,[first['id'],first['id']]).status_code==422
    assert client.get('/api/prelisting/batches').json()['total']==2
    assert client.get('/api/prelisting/batches?trash=true').json()['total']==0


def test_direct_delete_stops_all_preparation_states_and_preserves_saved_document(publishing):
    from catalog.listing_generation_jobs import start_job, checked_context
    from catalog.listing_ai_client import ListingAIError
    from test_listing_generation import AI
    from fastapi import HTTPException
    import pytest
    client,catalog,*_,created,base,remote=publishing
    job=start_job(catalog,client.app.state.walmart_publisher,created['id'],ai=AI(),schedule=False)
    with catalog.connect() as db:
        document=dict(db.execute('SELECT * FROM walmart_listing_documents WHERE batch_id=?',(created['id'],)).fetchone())
        for state in ('running','awaiting_sources'):
            db.execute('''INSERT INTO listing_generation_jobs SELECT ?,batch_id,?,revision,store_id,partner_id,
                binding_revision,input_hash,actor_id,report_json,message,started_at+1,updated_at
                FROM listing_generation_jobs WHERE id=?''',(state,state,job['id']))
        stale=dict(db.execute('SELECT * FROM listing_generation_jobs WHERE id=?',(job['id'],)).fetchone())
    result=change(client,[created['id']])
    assert result.status_code==200,result.text
    with catalog.connect() as db:
        assert all(row[0]=='interrupted' for row in db.execute('SELECT state FROM listing_generation_jobs WHERE batch_id=?',(created['id'],)))
        assert dict(db.execute('SELECT * FROM walmart_listing_documents WHERE batch_id=?',(created['id'],)).fetchone())==document
        with pytest.raises((ListingAIError,HTTPException)):
            checked_context(db,stale)
    assert not remote.posts and not remote.stock


def test_platform_submission_blocks_direct_cleanup_atomically(publishing):
    from test_walmart_publishing import ready
    client,catalog,*_,created,base,remote=publishing
    prepared=ready(publishing)
    response=client.post(base+'/validate',json={'revision':prepared['revision']})
    assert response.status_code==200,response.text
    doc=response.json()
    import uuid
    result=client.post(base+'/submit',json={'revision':doc['revision'],'validation_id':doc['validation']['id'],
        'request_key':str(uuid.uuid4()),'skus':[r['platform_sku'] for r in doc['rows']],
        'confirm_store_id':doc['store']['id'],'confirm_partner_id':doc['validation']['partner_id']})
    assert result.status_code==200,result.text
    before=len(remote.posts)
    assert change(client,[created['id']]).status_code==409
    assert not client.get(f"/api/prelisting/batches/{created['id']}").json()['deleted_at']
    assert len(remote.posts)==before


def test_manual_validation_waits_for_generation_to_finish(publishing):
    from catalog.listing_generation_jobs import start_job
    from test_listing_generation import AI
    client,catalog,*_,created,base,remote=publishing
    job=start_job(catalog,client.app.state.walmart_publisher,created['id'],ai=AI(),schedule=False)
    response=client.post(base+'/validate',json={'revision':job['revision']})
    assert response.status_code==409,response.text
    assert '生成' in response.json()['detail']
    assert not remote.posts and not remote.stock


def test_bulk_cleanup_requires_write_role_and_csrf(team):
    viewer,_=login(team,'viewer')
    assert change(viewer,['missing']).status_code==403
    admin,_=login(team)
    assert admin.post('/api/prelisting/batches/bulk-action',json={'ids':['missing'],'action':'delete'},
                      headers={'X-CSRF-Token':''}).status_code==403
