"""Batch-list cleanup preserves audit, identifiers and cancelled state."""
from fastapi.testclient import TestClient

from catalog.app import create_app
from test_prelisting import local, inputs, create
from test_auth import team, login


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


def test_mixed_status_or_missing_ids_are_atomic_and_duplicates_rejected(local):
    client,catalog=local
    first=cancelled(client);draft,_=create(client,inputs(client))
    assert change(client,[first['id'],draft['id']]).status_code==409
    assert change(client,[first['id'],'missing']).status_code==404
    assert change(client,[first['id'],first['id']]).status_code==422
    assert client.get('/api/prelisting/batches').json()['total']==2
    assert client.get('/api/prelisting/batches?trash=true').json()['total']==0


def test_bulk_cleanup_requires_write_role_and_csrf(team):
    viewer,_=login(team,'viewer')
    assert change(viewer,['missing']).status_code==403
    admin,_=login(team)
    assert admin.post('/api/prelisting/batches/bulk-action',json={'ids':['missing'],'action':'delete'},
                      headers={'X-CSRF-Token':''}).status_code==403
