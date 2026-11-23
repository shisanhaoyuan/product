import json
import pytest
from fastapi import HTTPException

from catalog.listing_generation_jobs import start_job, run_job
from catalog.listing_shared_collection import resume_sources
from test_listing_generation import AI, generation_image_downloads
from test_listing_price_review import amazon
from test_walmart_publishing import publishing
from test_giga_prelisting import warehouse
from test_giga import settings
from test_walmart_accounts import vault
from test_source_capture import page, offer


def begin(env):
    c,catalog,_,_,b,base,_=env
    job=start_job(catalog,c.app.state.walmart_publisher,b['id'],ai=AI(),schedule=False,source_mode='shared')
    assert job['state']=='awaiting_sources'
    assert len(job['report']['source_targets'])==2
    assert start_job(catalog,c.app.state.walmart_publisher,b['id'],ai=AI(),schedule=False,source_mode='shared')['id']==job['id']
    return job


def capture_all(env,job,location='10001'):
    c,*_=env
    receipts={}
    for target in job['report']['source_targets']:
        html=page(target['asin'],offer(price='85.49',original='89.99')).replace('10001',location)
        response=c.post(f"/api/products/{target['listing_id']}/source-capture/browser",json={
            'variant_sku':target['sku'],'source_url':f"https://www.amazon.com/dp/{target['asin']}",'html':html})
        assert response.status_code==200,response.text
        receipts[target['platform_sku']]=response.json()['capture_id']
    return receipts


def test_catalog_browser_quotes_are_used_without_a_second_collection(amazon,monkeypatch):
    c,catalog,_,_,b,base,_=amazon
    job=begin(amazon)
    captures=capture_all(amazon,job)
    def fail(*args,**kwargs):raise AssertionError('Filling must not start a separate Amazon browser')
    monkeypatch.setattr('catalog.images.fetch_amazon_page',fail)
    monkeypatch.setattr('catalog.amazon_browser.fetch_page',fail)
    ai=AI()
    ready=resume_sources(catalog,c.app.state.walmart_publisher,b['id'],job['id'],captures,ai=ai,schedule=False)
    assert ready['state']=='queued'
    assert ready['report']['timings']['stages']['collection']['count']==1
    assert ready['report']['source_wait_includes_human']
    assert resume_sources(catalog,c.app.state.walmart_publisher,b['id'],job['id'],captures,ai=ai,schedule=False)['id']==job['id']
    run_job(catalog,c.app.state.walmart_publisher,job['id'],ai)
    status=c.get(base+'/generation').json()['job']
    assert status['state']=='complete',status
    assert status['report']['timings']['source_wait_includes_human']
    assert status['report']['timings']['stages']['queue']['count']==1
    doc=c.get(base).json()
    assert all(row['orderable']['price']==107.99 for row in doc['rows'])
    assert all(row['source_collection']['method']=='browser' for row in status['report']['rows'].values())
    with catalog.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM source_captures').fetchone()[0]==2


@pytest.mark.parametrize('invalid', ['old','wrong_sku','wrong_postcode','missing','other_actor','superseded'])
def test_unrelated_or_stale_sources_cannot_resume_filling(amazon,invalid):
    c,catalog,_,_,b,base,_=amazon
    job=begin(amazon)
    captures=capture_all(amazon,job,'98101' if invalid=='wrong_postcode' else '10001')
    keys=list(captures)
    if invalid=='missing':captures.pop(keys[0])
    if invalid=='wrong_sku':captures[keys[0]]=captures[keys[1]]
    if invalid=='old':
        with catalog.connect() as db:
            db.execute('UPDATE source_captures SET captured_at=?',('2000-01-01T00:00:00+00:00',))
    if invalid=='superseded':capture_all(amazon,job)
    with pytest.raises(HTTPException) as error:
        resume_sources(catalog,c.app.state.walmart_publisher,b['id'],job['id'],captures,
                       actor_id=999 if invalid=='other_actor' else None,ai=AI(),schedule=False)
    assert error.value.status_code==409
    assert c.get(base+'/generation').json()['job']['state']=='awaiting_sources'


def test_concurrent_draft_edit_cannot_be_overwritten_by_browser_collection(amazon):
    c,catalog,_,_,b,base,_=amazon
    job=begin(amazon);captures=capture_all(amazon,job)
    doc=c.get(base).json();doc['rows'][0]['orderable']['price']=333
    assert c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).status_code==200
    with pytest.raises(HTTPException,match='其他操作修改'):
        resume_sources(catalog,c.app.state.walmart_publisher,b['id'],job['id'],captures,ai=AI(),schedule=False)
    assert c.get(base).json()['rows'][0]['orderable']['price']==333


def test_invalid_zip_then_manual_capture_can_resume_same_job(amazon,monkeypatch):
    c,catalog,_,_,b,base,_=amazon
    job=begin(amazon)
    capture_all(amazon,job,'98101')
    receipts=capture_all(amazon,job)
    resumed=resume_sources(catalog,c.app.state.walmart_publisher,b['id'],job['id'],receipts,ai=AI(),schedule=False)
    assert resumed['state']=='queued'


def test_http_generation_uses_shared_capture_and_can_cancel_wait(amazon,monkeypatch):
    import catalog.listing_generation_jobs as jobs
    c,catalog,_,_,b,base,_=amazon
    monkeypatch.setattr(jobs,'ListingAIClient',AI)
    result=c.post(base+'/generation')
    assert result.status_code==200,result.text
    job=result.json()
    assert job['state']=='awaiting_sources'
    assert c.post(base+f"/generation/{job['id']}/sources",json={'captures':{}}).status_code==409
    stopped=c.post(base+f"/generation/{job['id']}/cancel-sources")
    assert stopped.status_code==200 and stopped.json()['state']=='interrupted'
    retry=c.post(base+'/generation').json()
    assert retry['id']!=job['id'] and retry['state']=='awaiting_sources'


def test_changed_catalog_between_captures_is_not_silently_accepted(amazon):
    c,catalog,_,_,b,base,_=amazon
    job=begin(amazon)
    receipts=capture_all(amazon,job)
    # A manual source mutation breaks the capture-only digest chain.
    with catalog.connect() as db:
        row=db.execute('SELECT id,data_json FROM source_captures LIMIT 1').fetchone()
        record=json.loads(row['data_json']);record['source_digest_before']='different-source'
        db.execute('UPDATE source_captures SET data_json=? WHERE id=?',(json.dumps(record),row['id']))
    with pytest.raises(HTTPException,match='其他操作修改'):
        resume_sources(catalog,c.app.state.walmart_publisher,b['id'],job['id'],receipts,ai=AI(),schedule=False)
