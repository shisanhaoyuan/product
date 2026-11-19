"""Queue is a read projection; polling cannot submit feeds or change inventory."""
import json
from fastapi.testclient import TestClient
from catalog.app import create_app
from catalog.prelisting import encoded
from test_walmart_publishing import publishing, ready, send
from test_giga_prelisting import warehouse
from test_giga import settings
from test_walmart_accounts import vault
from test_auth import team, login, ORIGIN, PASSWORD


def queue(c, **params):
    response=c.get('/api/walmart-review',params=params)
    assert response.status_code==200,response.text
    return response.json()


def outcome(catalog,aid,status,**extra):
    with catalog.connect() as db:
        a=db.execute('SELECT rows_json FROM walmart_listing_attempts WHERE id=?',(aid,)).fetchone()
        results={r['platform_sku']:dict(status=status,message='测试结果',errors=[])|extra for r in json.loads(a[0])}
        db.execute('UPDATE walmart_listing_attempts SET results_json=? WHERE id=?',(encoded(results),aid))


def test_submission_moves_to_review_failure_returns_retry_moves_back(publishing):
    c,catalog,_,_,b,base,remote=publishing
    assert queue(c)['total']==0
    assert c.get('/api/prelisting/batches').json()['total']==1
    doc=ready(publishing);a=send(publishing,doc)
    assert c.get('/api/prelisting/batches').json()['total']==0
    rows=queue(c)
    assert rows['total']==2 and rows['counts']['processing']==2
    assert all(r['feed_id']==a['feed_id'] and r['batch_id']==b['id'] for r in rows['items'])
    outcome(catalog,a['id'],'failed',ingestion_status='DATA_ERROR')
    batches=c.get('/api/prelisting/batches',params={'status':'review_failed'}).json()
    assert batches['total']==1 and batches['items'][0]['review_failed_count']==2
    doc=ready(publishing);retry=send(publishing,doc)
    assert retry['id']!=a['id']
    assert c.get('/api/prelisting/batches').json()['total']==0
    assert queue(c,status='failed')['total']==0
    assert queue(c)['total']==2 and queue(c,history=True)['total']==4
    assert all(r['attempt_id']==retry['id'] for r in queue(c)['items'])
    assert queue(c,history=True,status='failed')['total']==2
    outcome(catalog,retry['id'],'published',stock_verified=True)
    assert queue(c,status='published')['total']==2
    assert c.get('/api/prelisting/batches').json()['total']==0


def test_partial_submission_keeps_unsubmitted_work_and_unknown_is_not_failure(publishing):
    c,catalog,_,_,b,base,remote=publishing
    doc=ready(publishing);a=send(publishing,doc,skus=[doc['rows'][0]['platform_sku']])
    batches=c.get('/api/prelisting/batches').json()
    assert batches['total']==1 and batches['items'][0]['unsubmitted_count']==1
    assert queue(c)['total']==1
    outcome(catalog,a['id'],'unknown')
    assert c.get('/api/prelisting/batches?status=review_failed').json()['total']==0
    assert queue(c,status='unknown')['total']==1


def test_review_images_match_submitted_skus_and_preserve_history(publishing):
    c,catalog,_,_,b,base,remote=publishing
    attempt=send(publishing,ready(publishing))
    with catalog.connect() as db:
        submitted=json.loads(db.execute('SELECT rows_json FROM walmart_listing_attempts WHERE id=?',
                                       (attempt['id'],)).fetchone()[0])
        for index,row in enumerate(submitted):
            row['visible']['mainImageUrl']=f'https://example.com/submitted-{index}.jpg'
        db.execute('UPDATE walmart_listing_attempts SET rows_json=? WHERE id=?',
                   (encoded(submitted),attempt['id']))
    before=len(remote.calls)
    expected={r['platform_sku']:r['visible']['mainImageUrl'] for r in submitted}
    assert {r['platform_sku']:r['image_url'] for r in queue(c)['items']}==expected
    assert len(remote.calls)==before
    outcome(catalog,attempt['id'],'failed')
    send(publishing,ready(publishing))
    history=queue(c,history=True)['items']
    assert {r['platform_sku']:r['image_url'] for r in history if r['attempt_id']==attempt['id']}==expected
    with catalog.connect() as db:
        submitted[0]['visible'].pop('mainImageUrl')
        submitted[1]['visible']['mainImageUrl']=None
        db.execute('UPDATE walmart_listing_attempts SET rows_json=? WHERE id=?',
                   (encoded(submitted),attempt['id']))
    assert all(r['image_url']=='' for r in queue(c,history=True)['items'] if r['attempt_id']==attempt['id'])


def test_filters_pagination_binding_scopes_and_read_does_not_contact_platform(publishing):
    c,catalog,_,_,b,base,remote=publishing
    doc=ready(publishing);a=send(publishing,doc)
    before=len(remote.calls)
    row=queue(c,page_size=1)['items'][0]
    assert len(remote.calls)==before
    assert queue(c,page_size=1,page=2)['total']==2
    for text in (row['platform_sku'],row['source_sku'],row['feed_id'],b['id'],row['store_name']):
        assert queue(c,q=text)['total']>=1
    assert queue(c,q='does_not_exist')['total']==0
    assert queue(c,store_id=9999)['total']==0
    assert queue(c,status='published')['total']==0
    assert c.get('/api/walmart-review?status=invalid').status_code==422
    assert c.get('/api/walmart-review?q='+('a'*201)).status_code==422
    reopened=TestClient(create_app(catalog.path))
    assert queue(reopened)['total']==2
    with catalog.connect() as db:
        db.execute("UPDATE walmart_accounts SET partner_id='other' WHERE store_id=?",(a['store_id'],))
    assert not queue(c)['items'][0]['can_refresh']
    response=c.post(f"/api/walmart-review/attempts/{a['id']}/refresh")
    assert response.status_code==409
    assert len(remote.posts)==1


def test_readonly_poll_and_failed_poll_preserve_original_results(publishing):
    c,catalog,_,_,b,base,remote=publishing
    a=send(publishing,ready(publishing));before=catalog.export()
    response=c.post(f"/api/walmart-review/attempts/{a['id']}/refresh")
    assert response.status_code==200,response.text
    assert all(r['status']=='stock_pending' for r in response.json()['results'].values())
    assert not any(r.method=='PUT' for r in remote.calls)
    assert len(remote.posts)==1 and catalog.export()==before
    old=queue(c)['items'];remote.owner_override='wrong'
    assert c.post(f"/api/walmart-review/attempts/{a['id']}/refresh").status_code==400
    assert queue(c)['items']==old
    assert c.post('/api/walmart-review/attempts/missing/refresh').status_code==404


def test_viewer_read_and_admin_only_polling(publishing,monkeypatch):
    c,catalog,_,_,b,base,remote=publishing
    a=send(publishing,ready(publishing))
    monkeypatch.setenv('CATALOG_AUTH_ENABLED','1')
    monkeypatch.setenv('CATALOG_PUBLIC_ORIGIN',ORIGIN)
    app=create_app(catalog.path)
    app.state.auth.create_user('viewer',PASSWORD,role='viewer',temporary=False)
    client,viewer=login(app,'viewer')
    assert client.get('/api/walmart-review').status_code==200
    response=client.post(f"/api/walmart-review/attempts/{a['id']}/refresh",headers={'X-CSRF-Token':viewer.json()['csrf']})
    assert response.status_code==403


def test_mixed_results_return_only_failed_count_without_unlocking_success(publishing):
    c,catalog,_,_,b,base,remote=publishing
    a=send(publishing,ready(publishing))
    with catalog.connect() as db:
        rows=json.loads(db.execute('SELECT rows_json FROM walmart_listing_attempts WHERE id=?',(a['id'],)).fetchone()[0])
        result={rows[0]['platform_sku']:dict(status='published',message='已发布',errors=[],stock_verified=True),
                rows[1]['platform_sku']:dict(status='failed',message='审核失败',errors=[dict(code='ERR',message='缺少颜色',original='Missing color')])}
        db.execute('UPDATE walmart_listing_attempts SET results_json=? WHERE id=?',(encoded(result),a['id']))
    batch=c.get('/api/prelisting/batches?status=review_failed').json()['items'][0]
    assert batch['review_failed_count']==1 and batch['unsubmitted_count']==0
    assert queue(c,status='published')['total']==1 and queue(c,status='failed')['total']==1
    failure=queue(c,status='failed')['items'][0]
    assert failure['errors'][0]['message']=='缺少颜色'
    doc=c.get(base).json()
    assert doc['results'][rows[0]['platform_sku']]['status']=='published'
    assert c.post(base+'/submit',json=__import__('test_walmart_publishing').submission(doc,skus=[rows[0]['platform_sku']])).status_code==409
    assert len(remote.posts)==1


def test_same_sku_across_stores_never_collapses_and_search_is_literal(publishing):
    c,catalog,_,_,b,base,remote=publishing
    a=send(publishing,ready(publishing))
    with catalog.connect() as db:
        old=dict(db.execute('SELECT * FROM walmart_listing_attempts WHERE id=?',(a['id'],)).fetchone())
        sid=db.execute('SELECT id FROM listing_stores WHERE id<>? ORDER BY id LIMIT 1',(a['store_id'],)).fetchone()[0]
        old.update(id='other-store-attempt',store_id=sid,partner_id='different-partner',request_key='other-store-key')
        db.execute('INSERT INTO walmart_listing_attempts VALUES('+','.join('?' for _ in old)+')',tuple(old.values()))
    assert queue(c)['total']==4
    assert queue(c,store_id=sid)['total']==2
    assert all(r['store_id']==sid and not r['can_refresh'] for r in queue(c,store_id=sid)['items'])
    assert queue(c,q='%')['total']==0 and queue(c,q='_')['total']==0
    assert 'payload_json' not in str(queue(c)) and 'encrypted_credentials' not in str(queue(c))
