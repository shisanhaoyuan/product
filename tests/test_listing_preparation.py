"""The automatic preparation boundary uses mocks, isolated storage and no live model."""
import copy
import io
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from catalog.database import write_lock
from catalog.listing_ai_client import ListingAIClient, ListingAIError
from catalog.listing_generation_jobs import start_job, run_job, public_job
from catalog.listing_prerequisites import pool_assignment, resource_digest
from catalog.listing_repair import repair_fields, repair_row
from catalog.listing_result_worker import tick
from catalog.listing_source_evidence import amazon_details, supplement_facts
from catalog.listing_vision import allowed_image_url, capability, fetch_image, image_data, image_evidence
from catalog.prelisting import encoded
from catalog.walmart_listing_schema import sections
from test_listing_generation import AI, listing
from test_walmart_publishing import publishing, spec, PT, ready, send, submission
from test_auth import team
from test_walmart_accounts import vault
from test_giga_prelisting import warehouse
from test_giga import settings


def run(env,ai=None):
    c,catalog,*_,b,base,remote=env
    job=start_job(catalog,c.app.state.walmart_publisher,b['id'],ai=ai or AI(),schedule=False)
    run_job(catalog,c.app.state.walmart_publisher,job['id'],ai or AI())
    result=c.get(base+'/generation').json()['job']
    assert result['state']=='complete',result
    return c.get(base).json(),result


def edit(c,base,doc):
    response=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')})
    assert response.status_code==200,response.text
    return response.json()


def code(value='9780306406157',**kw):
    return dict(type='EAN',value=value,brand='NewBrand',proof='真实品牌商品归属证明') | kw


def test_closed_loop_saves_reopens_validates_is_idempotent_and_never_publishes(publishing):
    c,catalog,*_,base,remote=publishing
    before=c.get(base).json();source=catalog.export()
    after,job=run(publishing)
    assert after['validation']['current'] and after['validation']['valid_count']==2
    assert all(r['preparation_state']=='ready' for r in job['report']['rows'].values())
    assert c.get(base).json()['rows']==after['rows']
    ai=AI();same=start_job(catalog,c.app.state.walmart_publisher,publishing[4]['id'],ai=ai,schedule=False)
    assert same['id']==job['id'] and not ai.calls
    for old,new in zip(before['rows'],after['rows']):
        assert (old['source_sku'],old['platform_sku'],old['identification'])==(new['source_sku'],new['platform_sku'],new['identification'])
        assert old['orderable']['price']==new['orderable']['price']
        assert old['orderable']['msrp']==new['orderable']['msrp']
        assert old['visible']['mainImageUrl']==new['visible']['mainImageUrl']
    assert catalog.export()==source and not remote.posts and not remote.stock


class RepairAI(AI):
    def query(self,purpose,payload):
        if purpose!='repair_attributes':
            return super().query(purpose,payload)
        self.calls.append((purpose,payload))
        field=next(f for f in payload['fields'] if f.startswith('visible.'))
        return dict(source_sku=payload['source_sku'],actions=[dict(field=field,value='Black',kind='estimate',
            refs=['content.copy.title'],confidence=.6,reason='依据本 SKU 结构作普通款式判断，非供应商实测')])


def test_three_targeted_rounds_and_only_current_error_fields(publishing):
    c,catalog,*_,base,remote=publishing
    shape=sections(remote.schema,PT)['visible']
    for key in ('color','finish','style'):
        shape['properties'][key]={'type':'string','enum':['Black','White']};shape['required'].append(key)
    ai=RepairAI();doc,job=run(publishing,ai)
    assert doc['validation']['valid_count']==2
    assert all(len(row['rounds'])==3 for row in job['report']['rows'].values())
    repairs=[p for purpose,p in ai.calls if purpose=='repair_attributes']
    assert len(repairs)==6
    assert all(set(p['fields'])<=set('visible.'+k for k in ('color','finish','style')) for p in repairs)
    with catalog.connect() as db:
        assert db.execute('SELECT count(*) FROM listing_preparation_rounds').fetchone()[0]==6
    assert not remote.posts and not remote.stock


def test_no_progress_stops_after_one_repair_and_missing_reasons_survive(publishing):
    remote=publishing[-1];shape=sections(remote.schema,PT)['visible']
    shape['properties']['bedSize']={'type':'string','enum':['Queen','Non-Standard']};shape['required'].append('bedSize')
    doc,job=run(publishing)
    assert doc['validation']['valid_count']==0
    assert all(len(row['rounds'])==1 and row['preparation_state']=='needs_review' for row in job['report']['rows'].values())
    assert all('bedSize' not in r['visible'] for r in doc['rows'])


def test_manual_values_including_edit_and_restore_are_locked(publishing):
    c,catalog,*_,base,remote=publishing
    shape=sections(remote.schema,PT)['visible']
    shape['properties']['color']={'type':'string','enum':['Black','White']};shape['required'].append('color')
    doc,job=run(publishing,RepairAI())
    for row in doc['rows']:row['visible']['color']='White'
    doc=edit(c,base,doc)
    for row in doc['rows']:row['visible']['color']='Black'
    doc=edit(c,base,doc)
    _,job=run(publishing,RepairAI())
    for entry in job['report']['rows'].values():
        assert entry['fields']['visible.color']['manual_locked'] is True
        assert not entry['rounds']


def test_dependencies_do_not_unlock_unrelated_or_manual_controllers():
    row=listing();schema=spec();shape=sections(schema,PT)['visible']
    shape['properties'].update(style={'type':'string'},bedSize={'type':'string'},unrelated={'type':'string'})
    shape['allOf'] += [dict(if_={})]  # Unrelated structure must not broaden the patch.
    shape['allOf'].extend([{'if':{'properties':{'style':{'const':'Sleeper'}}},'then':{'required':['bedSize']}},
                          {'if':{'properties':{'unrelated':{'const':'x'}}},'then':{'required':['other']}}])
    row['visible'].update(style='Sleeper',unrelated='x')
    audit={f'visible.{key}':dict(kind='estimate',value=value) for key,value in [('style','Sleeper'),('unrelated','x')]}
    fields=repair_fields(row,schema,[dict(path='visible.bedSize',message='required')],audit)
    assert set(fields)=={'visible.style','visible.bedSize'}
    audit['visible.style']['manual_locked']=True
    assert set(repair_fields(row,schema,[dict(path='visible.bedSize',message='required')],audit))=={'visible.bedSize'}


def test_repair_rejects_cross_sku_protected_and_fake_visual_source():
    row=listing();schema=spec();shape=sections(schema,PT)['visible'];shape['properties']['color']={'type':'string'}
    errors=[dict(path='visible.color',message='required')];audit={}
    class Evil(AI):
        def query(self,purpose,payload):
            return dict(source_sku='W1',actions=[dict(field='orderable.price',value=1)])
    with pytest.raises(ListingAIError,match='越权'):
        repair_row(row,schema,errors,{},Evil(),audit,1)
    class Fake(AI):
        def query(self,purpose,payload):
            return dict(source_sku='W1',actions=[dict(field='visible.color',value='Black',kind='source',
                refs=['visual_evidence.0'],confidence=.99,reason='伪造来源')])
    result,report=repair_row(row,schema,errors,{'visual_evidence':[{'value':'Black'}]},Fake(),audit,1)
    assert result==row and report['state']=='no_progress' and report['rejected']


def test_source_conflict_cannot_be_bypassed_by_manual_validate(publishing,monkeypatch):
    c,catalog,*_,base,remote=publishing
    from catalog.listing_generation import source_facts
    def conflict(catalog,source,item,variant,ai):
        return source_facts(source,item,variant),dict(state='conflict',message='同 SKU 规格存在冲突，请刷新来源')
    monkeypatch.setattr('catalog.listing_generation_jobs.supplement_facts',conflict)
    doc,job=run(publishing)
    assert doc['validation']['valid_count']==0
    validated=c.post(base+'/validate',json={'revision':doc['revision']}).json()
    assert validated['validation']['valid_count']==0
    assert all(any('冲突' in e['message'] for e in v['errors']) for v in validated['validation']['rows'].values())
    assert not remote.posts


def test_readiness_expires_and_resources_change_invalidates_submit(publishing):
    c,catalog,*_,b,base,remote=publishing
    doc=ready(publishing);assert doc['validation']['current']
    with catalog.connect() as db:
        report=json.loads(db.execute('SELECT validation_json FROM walmart_listing_documents WHERE batch_id=?',(b['id'],)).fetchone()[0])
        report['at']=int(time.time())-601
        db.execute('UPDATE walmart_listing_documents SET validation_json=? WHERE batch_id=?',(encoded(report),b['id']))
    assert c.get(base).json()['validation']['current'] is False
    doc=c.post(base+'/validate',json={'revision':doc['revision']}).json()
    assert c.post('/api/walmart/identifiers',json={'items':[code()]}).status_code==200
    assert c.get(base).json()['validation']['current'] is False
    assert c.post(base+'/submit',json=submission(doc)).status_code==409
    assert not remote.posts


def test_actual_store_defaults_exemption_guard_and_revision(publishing):
    c,catalog,*_,b,base,remote=publishing;sid=b['preview']['store']['id'];path=f'/api/walmart/accounts/{sid}/preparation'
    options=c.post(path+'/options');assert options.status_code==200
    assert [r['id'] for r in options.json()['nodes']]==[remote.node]
    config=dict(revision=0,fulfillment_center_id='fake',exemptions=[])
    assert c.put(path,json=config).status_code==422
    config.update(fulfillment_center_id=remote.node,exemptions=[dict(brand='NewBrand',product_type=PT,category='Furniture',submission_id='approved-123')])
    remote.approved=False
    assert c.put(path,json=config).status_code==422
    remote.approved=True
    saved=c.put(path,json=config);assert saved.status_code==200,saved.text
    assert c.put(path,json=config).status_code==409
    doc=c.get(base).json()
    for row in doc['rows']:row['identification'].update(value='')
    edit(c,base,doc)
    after,job=run(publishing)
    assert after['validation']['valid_count']==2
    assert all(r['identification']['mode']=='exemption' and r['fulfillment_center_id']==remote.node for r in after['rows'])
    assert all(r['fields']['identification']['kind']=='verified_prerequisite' for r in job['report']['rows'].values())


def test_store_default_brand_is_persistent_scoped_and_does_not_change_batches(publishing):
    c,catalog,*_,b,base,remote=publishing
    sid=b['preview']['store']['id'];path=f'/api/walmart/accounts/{sid}/preparation'
    before=c.get(base).json();source=catalog.export()
    assert c.get(path).json()['default_brand']==''
    response=c.put(path,json=dict(revision=0,default_brand='  Growlume  ',fulfillment_center_id='',exemptions=[]))
    assert response.status_code==200,response.text
    assert response.json()['default_brand']=='Growlume'
    stores=c.get('/api/listing-stores').json()
    assert next(row for row in stores if row['id']==sid)['default_brand']=='Growlume'
    assert all(not row['default_brand'] for row in stores if row['id']!=sid)
    cards=c.get('/api/walmart/accounts').json()['stores']
    assert next(row for row in cards if row['store_id']==sid)['default_brand']=='Growlume'
    assert c.get(base).json()['rows']==before['rows'] and catalog.export()==source
    response=c.put(path,json=dict(revision=1,fulfillment_center_id='',exemptions=[]))
    assert response.status_code==200 and response.json()['default_brand']=='Growlume'
    assert c.put(path,json=dict(revision=1,default_brand='Other')).status_code==409
    with catalog.connect() as db:
        db.execute('UPDATE walmart_accounts SET partner_id=? WHERE store_id=?',('another-partner',sid))
    assert c.get(path).json()['default_brand']==''
    assert next(row for row in c.get('/api/listing-stores').json() if row['id']==sid)['default_brand']==''


def test_cancelled_batch_cannot_be_deleted_while_generation_is_active(publishing):
    c,catalog,*_,b,base,remote=publishing
    job=start_job(catalog,c.app.state.walmart_publisher,b['id'],ai=AI(),schedule=False)
    assert c.post(f"/api/prelisting/batches/{b['id']}/action",json={'action':'cancel'}).status_code==200
    payload=dict(ids=[b['id']],action='delete')
    assert c.post('/api/prelisting/batches/bulk-action',json=payload).status_code==409
    with catalog.connect() as db:
        db.execute("UPDATE listing_generation_jobs SET state='interrupted' WHERE id=?",(job['id'],))
    assert c.post('/api/prelisting/batches/bulk-action',json=payload).json()['changed']==1


def test_pool_atomic_assignment_equivalent_dedup_ownership_and_cross_store_reuse(publishing):
    c,catalog,*_=publishing
    response=c.post('/api/walmart/identifiers',json={'items':[code()]});assert response.status_code==200
    assert c.post('/api/walmart/identifiers',json={'items':[code()]}).status_code==200
    assert c.post('/api/walmart/identifiers',json={'items':[code('09780306406157',type='GTIN')]}).status_code==200
    assert c.post('/api/walmart/identifiers',json={'items':[code(brand='Other')]}).status_code==409
    def assign(sku):
        with catalog.connect() as db:
            write_lock(db)
            return pool_assignment(db,'GigaB2B',sku,'NewBrand')
    with ThreadPoolExecutor(max_workers=2) as workers:
        results=list(workers.map(assign,['W1','W2']))
    assert sum(bool(r) for r in results)==1
    allocated=next(i for i,r in enumerate(results) if r)
    repeated=assign(['W1','W2'][allocated])
    assert repeated['value']=='9780306406157'
    assert assign('other') is None
    assert len(c.get('/api/walmart/identifiers').json()['items'])==1


def test_distinct_variants_cannot_share_real_code(publishing):
    c,*_,base,remote=publishing;doc=ready(publishing)
    doc['rows'][1]['identification']=copy.deepcopy(doc['rows'][0]['identification'])
    doc=edit(c,base,doc)
    checked=c.post(base+'/validate',json={'revision':doc['revision']}).json()
    assert checked['validation']['valid_count']==0
    assert all(any('同一真实编码' in i['message'] for i in r['errors']) for r in checked['validation']['rows'].values())


def test_result_worker_recovers_lease_is_readonly_and_never_resubmits(publishing):
    c,catalog,*_,base,remote=publishing;doc=ready(publishing);a=send(publishing,doc)
    aid=a['id'];path=base+'/attempts/'+aid+'/refresh'
    with catalog.connect() as db:
        db.execute("UPDATE listing_result_tasks SET state='running',token='crashed',lease_until=?,next_at=0 WHERE attempt_id=?",(int(time.time())-1,aid))
    count=len(remote.posts);assert tick(c.app)
    assert not remote.stock and len(remote.posts)==count
    current=c.get(base).json();assert all(r['status']=='stock_pending' for r in current['results'].values())
    assert all(r.method!='PUT' for r in remote.calls)
    manual=c.post(path);assert manual.status_code==200,manual.text
    assert all(r['status']=='published' for r in manual.json()['results'].values())
    with catalog.connect() as db:db.execute('UPDATE listing_result_tasks SET next_at=0 WHERE attempt_id=?',(aid,))
    assert tick(c.app)
    task=c.get(base).json()['attempts'][0]['polling'];assert task['state']=='complete'
    assert len(remote.posts)==count


def test_unknown_submission_is_not_enqueued_or_retried(publishing):
    c,catalog,*_,base,remote=publishing;remote.fail='timeout';a=send(publishing,ready(publishing))
    assert a['state']=='unknown'
    assert tick(c.app) is False
    assert len(remote.posts)==1


def png():
    out=io.BytesIO();Image.new('RGB',(20,10),'red').save(out,format='PNG');return out.getvalue()


def test_image_assets_have_stable_digest_and_vision_cache_is_sku_scoped(publishing,monkeypatch):
    _,catalog,*_=publishing
    monkeypatch.setattr('catalog.listing_vision.fetch_image',lambda url:png())
    digest,data=image_data(catalog,'https://media-amazon.com/1.png')
    assert image_data(catalog,'/api/products/images/assets/'+digest)[0]==digest
    monkeypatch.setattr('catalog.listing_vision.capability',lambda catalog,ai:None)
    row=listing();row['visible']['mainImageUrl']='/api/products/images/assets/'+digest
    class Vision(AI):
        settings=SimpleNamespace(model='test',vision=True)
        def query(self,purpose,payload):
            self.calls.append((purpose,payload))
            return dict(source_sku=payload['source_sku'],observations=[dict(attribute='color',value='Red',
                kind='visual_inference',images=payload['images'],confidence=.9,reason='图中可见红色，不推断尺寸')])
    ai=Vision();variant={'content':{}}
    observations,report=image_evidence(catalog,row,variant,ai)
    assert observations[0]['kind']=='visual_inference' and report['state']=='complete'
    assert image_evidence(catalog,row,variant,ai)[1]['cached'] and len(ai.calls)==1
    row['source_sku']='W2';image_evidence(catalog,row,variant,ai)
    assert len(ai.calls)==2
    with catalog.connect() as db:assert db.execute('SELECT count(*) FROM listing_image_evidence').fetchone()[0]==2


@pytest.mark.parametrize('url',['http://media-amazon.com/x','https://user:secret@media-amazon.com/x','https://media-amazon.com.evil.test/x','https://127.0.0.1/x','https://media-amazon.com:123/x'])
def test_images_reject_unsafe_destinations(url):
    with pytest.raises(ListingAIError):allowed_image_url(url)


def test_image_redirect_to_private_destination_is_rejected(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('1.1.1.1',443))])
    def handler(request):return httpx.Response(302,headers={'location':'https://127.0.0.1/private'})
    with pytest.raises(ListingAIError):fetch_image('https://media-amazon.com/x',httpx.MockTransport(handler))
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('10.0.0.1',443))])
    with pytest.raises(ListingAIError,match='内网'):allowed_image_url('https://media-amazon.com/x')


def test_model_capability_probe_requires_actual_image_answer(publishing):
    catalog=publishing[1]
    class Blind(AI):
        def query(self,*args):return {'colors':['none']*4}
    with pytest.raises(ListingAIError,match='能力探测'):capability(catalog,Blind())


def test_multimodal_transport_keeps_images_out_of_source_text_and_redacts_errors():
    settings=SimpleNamespace(configured=True,model='mock',base_url='https://mock.test/v1',stream=False,key='test-private')
    def handler(request):
        body=json.loads(request.content);content=body['messages'][1]['content']
        assert content[0]['type']=='text' and '_images' not in content[0]['text']
        assert content[1]['image_url']['url'].startswith('data:image/')
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}]})
    ai=ListingAIClient(settings,httpx.MockTransport(handler))
    assert ai.query('image_evidence',{'_images':['data:image/jpeg;base64,AA==']})=={'ok':True}
    failing=ListingAIClient(settings,httpx.MockTransport(lambda request:httpx.Response(401,text=settings.key)))
    with pytest.raises(ListingAIError) as e:failing.query('x',{})
    assert settings.key not in str(e.value)


def test_amazon_supplement_checks_exact_asin_and_current_product_content():
    html='<input id="ASIN" value="B012345678"><span id="productTitle">Own chair</span><div id="feature-bullets"><ul><li>Own feature</li></ul></div><section id="productOverview_feature_div"><table><tr><th>Color</th><td>Black</td></tr></table></section><div id="recommendations">Other seller desk</div>'
    result=amazon_details(html,'B012345678')
    assert result['content.copy']['features']==['Own feature'] and result['content.attributes']=={'Color':'Black'}
    assert 'Other seller desk' not in str(result)
    with pytest.raises(ListingAIError):amazon_details(html,'B000000000')


def test_captcha_supplement_preserves_same_sku_frozen_facts_and_returns_handoff(publishing,monkeypatch):
    from catalog.images import HumanVerificationRequired
    monkeypatch.setattr('catalog.images.fetch_amazon_page',lambda asin: (_ for _ in ()).throw(HumanVerificationRequired('captcha')))
    source={'source':'Amazon','sku':'W1'};item={'listing_id':'one','category':'Chair'}
    variant={'sku':'W1','asin':'B012345678','content':{'copy':{'title':'Own chair'}}}
    facts,report=supplement_facts(publishing[1],source,item,variant,SimpleNamespace(settings=SimpleNamespace(supplement=True)))
    assert facts['content.copy']['title']=='Own chair' and report['state']=='verification_required'
    assert report['listing_id']=='one' and report['asin']=='B012345678'


def test_platform_attribute_error_drives_new_draft_without_resubmission(publishing,monkeypatch):
    c,catalog,*_,b,base,remote=publishing
    shape=sections(remote.schema,PT)['visible'];shape['properties']['color']={'type':'string','enum':['Black','White']};shape['required'].append('color')
    doc,prior=run(publishing,RepairAI());attempt=send(publishing,doc)
    for sku in attempt['skus']:remote.item_status[sku]='DATA_ERROR'
    started=[]
    def queue(catalog,publisher,bid,actor):
        result=start_job(catalog,publisher,bid,actor,ai=RepairAI(),schedule=False);started.append(result);return result
    monkeypatch.setattr('catalog.listing_result_worker.ListingAISettings',lambda:SimpleNamespace(configured=True))
    monkeypatch.setattr('catalog.listing_generation_jobs.start_job',queue)
    with catalog.connect() as db:db.execute('UPDATE listing_result_tasks SET next_at=0')
    assert tick(c.app) and len(started)==1
    assert started[0]['id']!=prior['id']
    class White(RepairAI):
        def query(self,purpose,payload):
            result=super().query(purpose,payload)
            if purpose=='repair_attributes':
                for action in result['actions']:action['value']='White'
            return result
    run_job(catalog,c.app.state.walmart_publisher,started[0]['id'],White())
    current=c.get(base).json()
    assert current['validation']['valid_count']==2,current['validation']
    assert all(row['visible']['color']=='White' for row in current['rows'])
    assert len(remote.posts)==1 and not remote.stock


def test_shared_validation_uses_stable_same_sku_image_without_editing_gallery(publishing,monkeypatch):
    from catalog.listing_vision import stable_feed_row
    c,catalog,*_,base,remote=publishing
    monkeypatch.setattr('catalog.listing_vision.fetch_image',lambda url:png())
    monkeypatch.setattr('catalog.listing_vision.capability',lambda *args:None)
    doc=ready(publishing);row=doc['rows'][0];original=copy.deepcopy(row)
    class Vision(AI):
        settings=SimpleNamespace(model='vision',vision=True)
        def query(self,purpose,payload):return dict(source_sku=payload['source_sku'],observations=[])
    image_evidence(catalog,row,{},Vision())
    with catalog.connect() as db:
        stable=stable_feed_row(db,row)
    assert row==original and stable['visible']['mainImageUrl'].startswith('/api/products/images/assets/')
    other=copy.deepcopy(row);other['source_sku']='OTHER'
    with catalog.connect() as db:assert stable_feed_row(db,other)==other


def test_task_lease_prevents_duplicate_concurrent_polling(publishing):
    c,catalog,*_,base,remote=publishing;a=send(publishing,ready(publishing))
    with catalog.connect() as db:
        db.execute("UPDATE listing_result_tasks SET state='running',lease_until=?,next_at=0",(int(time.time())+300,))
    count=len(remote.calls)
    assert tick(c.app) is False and len(remote.calls)==count


def test_failed_schema_does_not_replace_existing_manual_attributes(publishing):
    c,catalog,*_,base,remote=publishing
    shape=sections(remote.schema,PT)['visible'];shape['properties']['color']={'type':'string','enum':['Black']};shape['required'].append('color')
    doc=c.get(base).json()
    for row in doc['rows']:row['visible']['color']='Manual Blue'
    edit(c,base,doc)
    current,job=run(publishing,RepairAI())
    assert current['validation']['valid_count']==0
    assert all(row['visible']['color']=='Manual Blue' for row in current['rows'])
    assert not remote.posts and not remote.stock


def test_discrete_bed_size_and_sleeper_semantics_are_enforced_in_code():
    from catalog.listing_generation import fill_row, semantic_patch_error
    facts={'content.copy':{'title':'Modular Sleeper Sofa'}}
    assert semantic_patch_error('visible.bedSize','Non-Standard',facts)
    assert semantic_patch_error('visible.bedSize','Queen',{'content.copy':{'title':'Chair'}})
    assert not semantic_patch_error('visible.bedSize','Queen',facts)
    assert not semantic_patch_error('visible.bedSize','Non-Standard',{'content.copy':{'title':'Custom-size sleeper mattress'}})
    assert semantic_patch_error('visible.style',['Modular'],facts,['Sleeper','Modular'])
    assert not semantic_patch_error('visible.style',['Sleeper','Modular'],facts,['Sleeper'])
    row=listing();shape=spec();visible=sections(shape,PT)['visible']
    visible['properties']['bedSize']={'type':'string','enum':['Queen','Non-Standard']}
    visible['required'].append('bedSize')
    class Shortcut(AI):
        def query(self,purpose,payload):
            return dict(source_sku='W1',actions=[dict(field='visible.bedSize',value='Non-Standard',kind='estimate',
                refs=['content.copy.title'],confidence=.8,reason='床面尺寸未知')])
    filled,issues=fill_row(row,shape,facts,Shortcut(),{})
    assert 'bedSize' not in filled['visible']
    assert any('非标准' in issue['message'] for issue in issues)
    result,report=repair_row(row,shape,[dict(path='visible.bedSize',message='required')],facts,Shortcut(),{},1)
    assert result==row and report['rejected'] and report['state']=='no_progress'


def test_bed_size_vision_receives_options_and_invalidates_old_context(publishing,monkeypatch):
    _,catalog,*_=publishing
    monkeypatch.setattr('catalog.listing_vision.fetch_image',lambda url:png())
    monkeypatch.setattr('catalog.listing_vision.capability',lambda catalog,ai:None)
    row=listing();shape=spec();visible=sections(shape,PT)['visible']
    visible['properties']['bedSize']={'type':'string','enum':['Twin','Full','Queen']}
    class Vision(AI):
        settings=SimpleNamespace(model='test',vision=True)
        def query(self,purpose,payload):
            self.calls.append((purpose,payload))
            return dict(source_sku=payload['source_sku'],observations=[dict(attribute='bedSize',value='Full',
                kind='visual_inference',images=payload['images'],confidence=.44,
                reason='展开睡眠面比例最接近 Full，图片没有清晰尺寸，存在不确定性')])
    ai=Vision();before=copy.deepcopy(row)
    variant={'content':{'copy':{'title':'Sleeper Sofa'}}}
    observations,report=image_evidence(catalog,row,variant,ai,shape)
    assert report['state']=='complete' and observations[0]['value']=='Full'
    assert ai.calls[0][1]['fields']['bedSize']['enum']==['Twin','Full','Queen']
    image_evidence(catalog,row,variant,ai,shape)
    assert len(ai.calls)==1
    visible['properties']['bedSize']['enum']=['Queen']
    image_evidence(catalog,row,variant,ai,shape)
    assert len(ai.calls)==2 and row==before


def test_visual_bed_size_uses_best_low_confidence_without_text_model_agreement():
    from catalog.listing_generation import fill_row
    row=listing();shape=spec();visible=sections(shape,PT)['visible']
    visible['properties']['bedSize']={'type':'string','enum':['Twin','Full','Queen']}
    visible['required'].append('bedSize')
    facts={'visual_evidence':[dict(attribute='bedSize',value=value,kind='visual_inference',
        images=['same-sku-image'],confidence=confidence,reason='按展开床面比例估计，非实测')
        for value,confidence in [('Twin',.2),('Full',.48),('Queen',.32),('invented',.99)]]}
    audit={};before=copy.deepcopy(row)
    filled,issues=fill_row(row,shape,facts,AI(),audit)
    assert filled['visible']['bedSize']=='Full'
    assert audit['visible.bedSize']['kind']=='visual_inference'
    assert audit['visible.bedSize']['confidence']==.48
    assert audit['visible.bedSize']['refs']==['visual_evidence.1']
    assert not any(i['path']=='visible.bedSize' for i in issues)
    assert row==before and filled['orderable']['price']==row['orderable']['price']
    row['visible']['bedSize']='Queen'
    assert fill_row(row,shape,facts,AI(),{})[0]['visible']['bedSize']=='Queen'


def test_all_ordinary_missing_fields_select_highest_valid_candidate_in_fill_and_repair():
    from catalog.listing_generation import fill_row
    row=listing();shape=spec();visible=sections(shape,PT)['visible']
    for key in ('bedSize','seat_back_cushion_style'):
        visible['properties'][key]={'type':'string','enum':['First','Best','Other']}
        visible['required'].append(key)
    class Ranked(AI):
        def query(self,purpose,payload):
            result=super().query(purpose,payload)
            for field in payload['fields']:
                if field not in ('visible.bedSize','visible.seat_back_cushion_style'):
                    continue
                result['actions'].append(dict(field=field,candidates=[dict(value=value,kind='estimate',refs=[],
                    confidence=confidence,reason='来源不全，按当前商品用途选最可能候选，非来源事实')
                    for value,confidence in [('First',.12),('Best',.45),('Other',.23),('INVALID',.99)]]))
            return result
    audit={};filled,_=fill_row(row,shape,{},Ranked(),audit)
    for key in ('bedSize','seat_back_cushion_style'):
        assert filled['visible'][key]=='Best'
        assert audit['visible.'+key]['confidence']==.45
        assert audit['visible.'+key]['selection_policy']=='user_authorized_highest_confidence'
    errors=[dict(path='visible.'+key,message='缺少必填项') for key in ('bedSize','seat_back_cushion_style')]
    audit={};fixed,report=repair_row(row,shape,errors,{},Ranked(),audit,1)
    assert report['state']=='changed' and fixed['visible']['bedSize']=='Best'
    assert fixed['identification']==row['identification'] and fixed['orderable']==row['orderable']
    assert fixed['visible']['mainImageUrl']==row['visible']['mainImageUrl']


def test_job_persists_same_sku_visual_bed_size_and_reopens(publishing,monkeypatch):
    c,catalog,*_,base,remote=publishing
    visible=sections(remote.schema,PT)['visible']
    visible['properties']['bedSize']={'type':'string','enum':['Twin','Full','Queen']}
    visible['required'].append('bedSize')
    seen=[]
    def visual(catalog,row,variant,ai,schema):
        assert sections(schema,PT)['visible']['properties']['bedSize']['enum']==['Twin','Full','Queen']
        seen.append(row['source_sku'])
        value='Full' if row['source_sku']=='W1' else 'Queen'
        return [dict(attribute='bedSize',value=value,kind='visual_inference',confidence=.41,
            images=[row['source_sku']+'-image'],reason='按本 SKU 展开床面比例推测，非实测')],dict(state='complete')
    monkeypatch.setattr('catalog.listing_generation_jobs.image_evidence',visual)
    source=catalog.export();before=c.get(base).json()
    doc,job=run(publishing)
    assert set(seen)=={'W1','W2'}
    assert doc['validation']['valid_count']==2
    assert c.get(base).json()['rows']==doc['rows']
    for old,row in zip(before['rows'],doc['rows']):
        assert row['visible']['bedSize']==('Full' if row['source_sku']=='W1' else 'Queen')
        assert row['orderable']['price']==old['orderable']['price']
        assert row['identification']==old['identification']
        assert row['visible']['mainImageUrl']==old['visible']['mainImageUrl']
        entry=job['report']['rows'][row['platform_sku']]
        assert entry['fields']['visible.bedSize']['confidence']==.41
    assert catalog.export()==source and not remote.posts and not remote.stock


def test_new_preparation_endpoints_are_admin_only(team,vault):
    from test_auth import login
    admin,_=login(team)
    for role in ('editor','viewer'):
        user,_=login(team,role)
        for method,path,body in (
            ('get','/api/walmart/identifiers',None),
            ('post','/api/walmart/identifiers',{'items':[code()]}),
            ('get','/api/walmart/accounts/1/preparation',None),
            ('post','/api/walmart/accounts/1/preparation/options',{}),
            ('put','/api/walmart/accounts/1/preparation',{}),
        ):
            assert getattr(user,method)(path,**({'json':body} if body is not None else {})).status_code==403
    assert admin.post('/api/walmart/identifiers',json={'items':[code()]}).status_code==200
    assert len(admin.get('/api/walmart/identifiers').json()['items'])==1


def test_worker_rechecks_original_admin_before_any_remote_query(publishing):
    c,catalog,*_,base,remote=publishing
    attempt=send(publishing,ready(publishing));before=len(remote.calls)
    # Switch this isolated app to team mode with a revoked actor. Absence of a
    # matching active administrator must pause before touching Walmart.
    from catalog.auth import AuthStore
    c.app.state.auth=AuthStore(catalog)
    with catalog.connect() as db:
        db.execute('UPDATE listing_result_tasks SET actor_id=9999,next_at=0')
    assert tick(c.app)
    assert len(remote.calls)==before
    with catalog.connect() as db:
        task=db.execute('SELECT * FROM listing_result_tasks WHERE attempt_id=?',(attempt['id'],)).fetchone()
        assert task['state']=='paused' and '权限' in task['last_error']


def test_worker_recovers_queued_repair_and_tracks_completion(publishing,monkeypatch):
    c,catalog,*_,b,base,remote=publishing
    doc=ready(publishing);attempt=send(publishing,doc)
    for sku in attempt['skus']:remote.item_status[sku]='DATA_ERROR'
    original_start=start_job
    def queue(catalog,publisher,bid,actor):
        return original_start(catalog,publisher,bid,actor,ai=AI(),schedule=False)
    monkeypatch.setattr('catalog.listing_result_worker.ListingAISettings',lambda:SimpleNamespace(configured=True))
    monkeypatch.setattr('catalog.listing_generation_jobs.start_job',queue)
    with catalog.connect() as db:db.execute('UPDATE listing_result_tasks SET next_at=0')
    assert tick(c.app)
    with catalog.connect() as db:
        task=dict(db.execute('SELECT * FROM listing_result_tasks').fetchone())
        assert task['state']=='repair_pending' and task['repair_job_id']
        db.execute('UPDATE listing_result_tasks SET next_at=0')
    dispatched=[]
    monkeypatch.setattr('catalog.listing_result_worker.ListingAIClient',lambda:AI(),raising=False)
    monkeypatch.setattr('catalog.listing_ai_client.ListingAIClient',lambda:AI())
    monkeypatch.setattr('catalog.listing_generation_jobs.POOL',SimpleNamespace(submit=lambda *args:dispatched.append(args)))
    assert tick(c.app) and len(dispatched)==1
    assert dispatched[0][3]==task['repair_job_id']
    run_job(catalog,c.app.state.walmart_publisher,task['repair_job_id'],AI())
    with catalog.connect() as db:db.execute('UPDATE listing_result_tasks SET next_at=0')
    assert tick(c.app)
    with catalog.connect() as db:
        assert db.execute('SELECT state FROM listing_result_tasks').fetchone()[0]=='complete'
        assert db.execute('SELECT count(*) FROM listing_generation_jobs').fetchone()[0]==1
    assert len(remote.posts)==1 and not remote.stock
