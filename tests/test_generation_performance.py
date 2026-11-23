"""Concurrency, timing and cache invariants; all upstream traffic is mocked."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from catalog.generation_timing import GenerationTimings, phase
from catalog.image_processing_cache import ImageProcessingCache, KeyedLocks
from catalog.listing_generation_jobs import run_job, start_job
from catalog.listing_vision import image_data, image_evidence
from catalog.source_images import collect_images
from catalog.walmart_listing_schema import sections
from test_editing import local, detail
from test_images import picture
from test_listing_generation import AI, generation_image_downloads
from test_walmart_publishing import publishing, PT
from test_giga_prelisting import warehouse
from test_giga import settings
from test_walmart_accounts import vault


def extra_attribute(remote):
    shape=sections(remote.schema,PT)['visible']
    shape['properties']['operatorLabel']={'type':'string','minLength':1}
    shape['required'].append('operatorLabel')


class VariantAI(AI):
    def __init__(self, barrier=None, action=None):
        super().__init__()
        self.barrier, self.action=barrier, action
        self.lock=threading.Lock()
        self.active=self.peak=0
        self.visited=[]

    def query(self,purpose,payload):
        if purpose!='required_attributes' or 'visible.operatorLabel' not in payload['fields']:
            return super().query(purpose,payload)
        sku=payload['source_sku']
        with self.lock:
            self.active+=1;self.peak=max(self.peak,self.active);self.visited.append(sku)
        try:
            if self.barrier:
                self.barrier.wait(timeout=8)
            if self.action:
                self.action(sku)
            return dict(source_sku=sku,actions=[dict(field='visible.operatorLabel',value='Label '+sku,
                kind='estimate',confidence=.8,refs=['content.copy.title'],reason='当前 SKU 的普通属性推断')])
        finally:
            with self.lock:self.active-=1


def generate(env, ai):
    c,catalog,_,_,batch,base,_=env
    job=start_job(catalog,c.app.state.walmart_publisher,batch['id'],ai=ai,schedule=False)
    run_job(catalog,c.app.state.walmart_publisher,job['id'],ai)
    return c.get(base).json(),c.get(base+'/generation').json()['job']


def test_two_skus_overlap_and_merge_without_mixing_fields(publishing):
    c,catalog,_,_,batch,base,remote=publishing
    extra_attribute(remote)
    before=c.get(base).json()
    ai=VariantAI(threading.Barrier(2))
    doc,job=generate(publishing,ai)
    assert job['state']=='complete',job
    assert ai.peak==2 and set(ai.visited)=={'W1','W2'}
    for old,row in zip(before['rows'],doc['rows']):
        assert row['visible']['operatorLabel']=='Label '+row['source_sku']
        for key in ('platform_sku','source_sku','listing_id','identification'):
            assert row[key]==old[key]
        for key in ('price','msrp'):
            assert row['orderable'][key]==old['orderable'][key]
    assert doc['validation']['valid_count']==2 and doc['validation']['current']
    assert not remote.posts and not remote.stock
    timing=job['report']['timings']
    assert timing['elapsed_ms']>0 and timing['stages']['ai_response']['count']==len(ai.calls)+len(ai.visited)
    assert timing['stages']['final_validation']['count']==1
    assert set(timing['rows'])=={r['platform_sku'] for r in doc['rows']}
    assert all(r['ai_response']['count']>=1 and r['validation']['count']>=1 for r in timing['rows'].values())
    assert job['report']['phase']=='complete' and job['report']['sku_parallel']==2
    assert timing['image_cache']['hits']>=1


def test_serial_switch_retains_same_output_contract(publishing,monkeypatch):
    monkeypatch.setenv('CATALOG_GENERATION_SKU_PARALLEL','1')
    extra_attribute(publishing[-1])
    ai=VariantAI()
    doc,job=generate(publishing,ai)
    assert job['state']=='complete' and job['report']['sku_parallel']==1
    assert ai.peak==1 and len(ai.visited)==2
    assert doc['validation']['valid_count']==2


def test_manual_edit_while_both_skus_are_computing_stops_both_writes(publishing):
    c,catalog,_,_,batch,base,remote=publishing
    extra_attribute(remote)
    edited=threading.Event()
    def edit(sku):
        if sku=='W1':
            doc=c.get(base).json();doc['rows'][0]['visible']['productName']='Manual title retained'
            response=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')})
            assert response.status_code==200,response.text
            edited.set()
        else:
            assert edited.wait(8)
    doc,job=generate(publishing,VariantAI(threading.Barrier(2),edit))
    assert job['state']=='failed'
    assert doc['rows'][0]['visible']['productName']=='NewBrand Manual title retained'
    assert all('operatorLabel' not in row['visible'] for row in doc['rows'])
    assert not remote.posts


def test_store_rebinding_during_parallel_generation_prevents_saving(publishing):
    c,catalog,_,_,batch,base,remote=publishing
    extra_attribute(remote)
    def rebind(sku):
        if sku=='W1':
            with catalog.connect() as db:
                db.execute('UPDATE walmart_accounts SET revision=revision+1')
        else:
            changed.wait(8)
        changed.set()
    changed=threading.Event()
    doc,job=generate(publishing,VariantAI(threading.Barrier(2),rebind))
    assert job['state']=='failed'
    assert all('operatorLabel' not in row['visible'] for row in doc['rows'])
    assert not remote.posts


def test_exclusive_timings_separate_ai_channel_wait(monkeypatch):
    clock=[0.0]
    monkeypatch.setattr('catalog.generation_timing.time.perf_counter',lambda:clock[0])
    timings=GenerationTimings()
    with timings.measure('collection','sku'):
        clock[0]=.01
        with timings.measure('ai_response','sku'):
            clock[0]=.03
            with phase('ai_wait'):
                clock[0]=.05
            clock[0]=.08
        clock[0]=.10
    values=timings.snapshot()
    assert values['stages']==values['rows']['sku']
    assert {k:v['ms'] for k,v in values['stages'].items()}=={
        'collection':30.0,'ai_response':50.0,'ai_wait':20.0}
    assert all(v['count']==1 for v in values['stages'].values())


def test_queue_time_is_saved_before_execution(publishing):
    c,catalog,_,_,batch,base,_=publishing
    ai=AI()
    job=start_job(catalog,c.app.state.walmart_publisher,batch['id'],ai=ai,schedule=False)
    with catalog.connect() as db:
        stored=db.execute('SELECT report_json FROM listing_generation_jobs WHERE id=?',(job['id'],)).fetchone()
        import json
        report=json.loads(stored[0]);report['queued_at_ms']=int(time.time()*1000)-1200
        db.execute('UPDATE listing_generation_jobs SET report_json=? WHERE id=?',(json.dumps(report),job['id']))
    waiting=c.get(base+'/generation').json()['job']
    assert waiting['report']['timings']['stages']['queue']['ms']>=1200
    run_job(catalog,c.app.state.walmart_publisher,job['id'],ai)
    done=c.get(base+'/generation').json()['job']
    assert done['report']['timings']['stages']['queue']['ms']>=1200
    assert done['report']['timings']['stages']['queue']['count']==1


def test_image_preprocessing_reused_across_image_threads_and_skus(local,monkeypatch):
    client,catalog=local
    url='https://example.com/image.jpg'
    calls=[]
    monkeypatch.setattr('catalog.listing_vision.fetch_image',lambda _:picture())
    first=collect_images(catalog,detail(client)['id'],'child-0',[url])
    asset=first['images'][0]
    import catalog.listing_vision as vision
    original=vision.Image.open
    def open_image(*args,**kwargs):
        calls.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(vision.Image,'open',open_image)
    cache=ImageProcessingCache()
    def read():
        with cache.activate():
            dimensions={};result=image_data(catalog,asset,dimensions)
            return result,dimensions
    with ThreadPoolExecutor(2) as workers:
        results=list(workers.map(lambda _:read(),range(2)))
    assert results[0]==results[1] and results[0][1]=={'width':48,'height':48}
    assert len(calls)==1 and cache.stats()['hits']==1
    # Repeated collection passes inherit the same cache in their image workers.
    with cache.activate():
        collect_images(catalog,detail(client)['id'],'child-0',[url])
    assert len(calls)==1
    # Each new job rechecks the actual bytes; missing files are not hidden forever.
    with ImageProcessingCache().activate():
        image_data(catalog,asset)
    assert len(calls)==2


def test_vision_cache_invalidates_for_model_context_and_rules(local,monkeypatch):
    client,catalog=local
    monkeypatch.setattr('catalog.listing_vision.fetch_image',lambda _:picture())
    monkeypatch.setattr('catalog.listing_vision.capability',lambda *args:None)
    lid=detail(client)['id']
    collection=collect_images(catalog,lid,'child-0',['https://example.com/image.jpg'])
    row=dict(listing_id=lid,source_sku='child-0',product_type=PT,visible={'mainImageUrl':collection['images'][0]})
    variant=dict(_collected_images=collection['images'],content={'copy':{'title':'Current chair'}})
    class VisionAI:
        settings=SimpleNamespace(vision=True,model='model-A')
        def __init__(self):self.calls=0
        def query(self,purpose,payload):
            self.calls+=1
            return dict(source_sku='child-0',observations=[])
    ai=VisionAI()
    with ImageProcessingCache().activate():
        _,one=image_evidence(catalog,row,variant,ai)
        _,two=image_evidence(catalog,row,variant,ai)
        assert ai.calls==1 and two['cached']
        variant['content']['copy']['title']='Different product context'
        image_evidence(catalog,row,variant,ai)
        assert ai.calls==2
        ai.settings=SimpleNamespace(vision=True,model='model-B')
        image_evidence(catalog,row,variant,ai)
        assert ai.calls==3
        monkeypatch.setattr('catalog.listing_generation.RULES_VERSION','test-rules-change')
        image_evidence(catalog,row,variant,ai)
        assert ai.calls==4
        # Even identical images cannot transfer another SKU's model response.
        row['source_sku']='second-child'
        from catalog.listing_ai_client import ListingAIError
        with pytest.raises(ListingAIError,match='SKU'):
            image_evidence(catalog,row,variant,ai)
        assert ai.calls==5


def test_image_preprocessing_cache_is_bounded_and_isolated():
    first=ImageProcessingCache(capacity=2,max_bytes=12)
    for index in range(3):first.put(('db',str(index)),(str(index),'1234',{}))
    assert first.get(('db','0')) is None and first.stats()['entries']==2
    assert first.stats()['bytes']==8
    assert ImageProcessingCache().get(('db','2')) is None


def test_unrelated_vision_requests_do_not_share_a_long_running_lock():
    locks=KeyedLocks()
    barrier=threading.Barrier(2)
    def independent(key):
        with locks.hold(key):
            barrier.wait(timeout=3)
    with ThreadPoolExecutor(2) as workers:
        list(workers.map(independent,('request-A','request-B')))
    assert not locks.entries
