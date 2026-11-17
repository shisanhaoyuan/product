import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from catalog.listing_ai_client import ListingAIClient, ListingAIError
from catalog.listing_generation import choose_type, fill_row, required_keys, mapped_value, source_facts, validate_generated
from catalog.listing_generation_jobs import start_job, run_job
from catalog.walmart_publishing import default_rows
from catalog.walmart_listing_schema import DEFAULT_VERSION
from test_walmart_publishing import publishing, spec, PT, ready
from test_walmart_accounts import vault
from test_giga_prelisting import warehouse
from test_giga import settings


class AI:
    settings = SimpleNamespace(model='gpt-6-astra')
    def __init__(self):
        self.calls=[]
        self.hook=None
    def query(self, purpose, payload):
        self.calls.append((purpose,payload))
        if self.hook:
            self.hook();self.hook=None
        if purpose=='product_type':
            return dict(source_sku=payload['source_sku'],product_type=payload['candidates'][0]['name'],
                        confidence=.95,refs=['category'],reason='根据来源类目')
        actions=[]
        for field in payload['fields']:
            if field=='visible.assembledProductHeight':
                actions.append(dict(field=field,value={'measure':32.5,'unit':'in'},kind='estimate',
                                    confidence=.7,refs=['content.copy'],reason='根据同 SKU 家具类型估算，未提供精确尺寸'))
            if field=='orderable.ShippingWeight':
                actions.append(dict(field=field,value=25,kind='estimate',confidence=.7,refs=['content.copy'],reason='按同 SKU 家具类型估算'))
        return dict(source_sku=payload['source_sku'],actions=actions,missing=[])


def listing():
    preview={'brand':'NewBrand','items':[{'listing_id':'id','variants':[
        dict(sku='W1',platform_sku='MY171234AB',price='10',content={
            'copy':{'title':'Chair','description':'Wood chair','features':['Wood']},'images':['https://example.com/a.jpg'],'ean':'012345678905'})]}]}
    row=default_rows(preview)[0];row['product_type']=PT;row['fulfillment_center_id']='node'
    return row


def test_schema_mapping_units_and_conditionals():
    schema=spec();row=listing();ai=AI();audit={}
    facts={'detail.assembledHeight':'82.55','detail.assembledLengthUnit':'cm','detail.weight':10,'detail.weightUnit':'kg','content.copy':{'title':'Chair'}}
    result,issues=fill_row(row,schema,facts,ai,audit)
    assert result['visible']['assembledProductHeight']=={'measure':32.5,'unit':'in'}
    assert result['orderable']['ShippingWeight']==22.0462
    assert any('明确来源' in i['message'] for i in issues)
    assert not ai.calls  # No AI required for legal disclosure, which needs evidence.
    assert row==listing()
    shape=schema['properties']['MPItem']['items']['properties']['Visible']['properties'][PT]
    assert 'prop65WarningText' in required_keys(shape,{'isProp65WarningRequired':'Yes'})
    assert 'prop65WarningText' not in required_keys(shape,{'isProp65WarningRequired':'No'})
    assert mapped_value('material',{'type':'array','items':{'type':'string'}},{'detail.mainMaterial':'Wood'})[0]==['Wood']


def test_ai_rejects_identity_injection_invalid_values_and_legal_estimates():
    row=listing();schema=spec();facts={'content.copy':{'title':'Chair'}}
    class Evil(AI):
        def query(self,purpose,payload):
            return dict(source_sku='WRONG',actions=[])
    result,issues=fill_row(row,schema,facts,Evil(),{})
    assert result==row and any('SKU' in i['message'] for i in issues)
    class Price(AI):
        def query(self,purpose,payload):
            return dict(source_sku='W1',actions=[dict(field='orderable.price',value=1)])
    result,issues=fill_row(row,schema,facts,Price(),{})
    assert result['orderable']['price']==10 and any('未知' in i['message'] for i in issues)


def test_category_checks_scope_candidates_and_indoor_conflict():
    row=listing();row['product_type']=''
    item={'category':'Furniture > Primary Living Space > Sofas'}
    facts={'category':item['category'],'detail.productName':'Living room sofa'}
    types=[dict(name='Sofas & Couches',group='Sofas',category='Furniture')]
    pt,audit=choose_type(row,item,facts,types,AI())
    assert pt=='Sofas & Couches'
    bad=copy.deepcopy(types);bad[0]['name']='Outdoor Sofas'
    with pytest.raises(ListingAIError):choose_type(row,item,facts,bad,AI())
    with pytest.raises(ListingAIError):
        source_facts({'source':'GigaB2B','sku':'OTHER','source_facts':{}},item,{'sku':'W1'})


def test_persistent_job_preserves_identity_prices_and_never_publishes(publishing):
    c,catalog,_,_,b,base,remote=publishing
    before=c.get(base).json();ai=AI()
    publisher=c.app.state.walmart_publisher
    job=start_job(catalog,publisher,b['id'],ai=ai,schedule=False)
    assert start_job(catalog,publisher,b['id'],ai=ai,schedule=False)['id']==job['id']
    run_job(catalog,publisher,job['id'],ai)
    status=c.get(base+'/generation').json()['job']
    assert status['state']=='complete',status
    doc=c.get(base).json()
    for old,row in zip(before['rows'],doc['rows']):
        assert row['product_type']==PT and row['fulfillment_center_id']==remote.node
        for key in ['source_sku','platform_sku','listing_id','identification']:assert row[key]==old[key]
        for key in ['price','msrp']:assert row['orderable'][key]==old['orderable'][key]
        assert row['visible']['assembledProductHeight']['measure']==32.5
        assert any('明确来源' in i['message'] for i in status['report']['rows'][row['platform_sku']]['issues'])
    assert len(remote.posts)==0 and remote.stock=={}
    assert c.get(base).json()['rows']==doc['rows']


def test_concurrent_edit_rejected_and_completed_source_maps_kept(publishing):
    c,catalog,_,_,b,base,_=publishing
    ai=AI()
    job=start_job(catalog,c.app.state.walmart_publisher,b['id'],ai=ai,schedule=False)
    def edit():
        doc=c.get(base).json();doc['rows'][0]['visible']['productName']='Manual title'
        assert c.patch(base,json={k:doc[k] for k in ['revision','version','rows']}).status_code==200
    ai.hook=edit
    run_job(catalog,c.app.state.walmart_publisher,job['id'],ai)
    assert c.get(base+'/generation').json()['job']['state']=='failed'
    assert c.get(base).json()['rows'][0]['visible']['productName']=='Manual title'


def test_client_stream_validates_completion_and_redacts_errors():
    settings=SimpleNamespace(configured=True,key='not-a-real-key',base_url='https://example.test/v1',model='gpt-6-astra',stream=True)
    def transport(req):
        body=json.loads(req.content)
        assert body['model']=='gpt-6-astra' and req.headers['Authorization']=='Bearer not-a-real-key'
        return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':{'content':'{"ok":true}'},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n')
    assert ListingAIClient(settings,httpx.MockTransport(transport)).query('test',{})=={'ok':True}
    for response in [httpx.Response(401,text='not-a-real-key'),httpx.Response(302,headers={'location':'https://evil.test'}),
                     httpx.Response(200,text='data: {"choices":[{"delta":{"content":"{}"}}]}\n\ndata: [DONE]\n\n')]:
        with pytest.raises(ListingAIError) as e:
            ListingAIClient(settings,httpx.MockTransport(lambda r:response)).query('test',{})
        assert 'not-a-real-key' not in str(e.value)


def test_interrupted_worker_cannot_resume_or_overwrite_new_job(publishing):
    c,catalog,_,_,b,base,_=publishing
    ai=AI();publisher=c.app.state.walmart_publisher
    old=start_job(catalog,publisher,b['id'],ai=ai,schedule=False)
    replacement=[]
    before=c.get(base).json()['rows']
    def expire_and_restart():
        with catalog.connect() as db:
            db.execute('UPDATE listing_generation_jobs SET updated_at=0 WHERE id=?',(old['id'],))
        assert c.get(base+'/generation').json()['job']['state']=='interrupted'
        replacement.append(start_job(catalog,publisher,b['id'],ai=AI(),schedule=False))
    ai.hook=expire_and_restart
    run_job(catalog,publisher,old['id'],ai)
    status=c.get(base+'/generation').json()['job']
    assert status['id']==replacement[0]['id'] and status['state']=='queued'
    assert c.get(base).json()['rows']==before
    with catalog.connect() as db:
        assert db.execute('SELECT state FROM listing_generation_jobs WHERE id=?',(old['id'],)).fetchone()[0]=='interrupted'


@pytest.mark.parametrize('change',['source','binding'])
def test_source_and_binding_changes_stop_writes(publishing,change):
    c,catalog,fake,settings,b,base,_=publishing
    ai=AI();publisher=c.app.state.walmart_publisher
    job=start_job(catalog,publisher,b['id'],ai=ai,schedule=False)
    before=c.get(base).json()['rows']
    def change_binding():
        if change=='source':
            from catalog.giga import sync_page
            fake.detail[0]['description']='Changed after generation started'
            sync_page(catalog,settings,1,client=fake)
            return
        with catalog.connect() as db:
            db.execute('UPDATE walmart_accounts SET revision=revision+1 WHERE store_id=?',(b['preview']['store']['id'],))
    ai.hook=change_binding
    run_job(catalog,publisher,job['id'],ai)
    assert c.get(base+'/generation').json()['job']['state']=='failed'
    assert c.get(base).json()['rows']==before


def test_complete_generation_is_idempotent_until_schema_changes(publishing):
    c,catalog,_,_,b,base,_=publishing
    ready(publishing)
    ai=AI();publisher=c.app.state.walmart_publisher
    job=start_job(catalog,publisher,b['id'],ai=ai,schedule=False)
    run_job(catalog,publisher,job['id'],ai)
    report=c.get(base+'/generation').json()['job']
    assert all(r['state']=='complete' for r in report['report']['rows'].values()),report
    doc=c.get(base).json()
    assert start_job(catalog,publisher,b['id'],ai=ai,schedule=False)['id']==job['id']
    assert c.get(base).json()['revision']==doc['revision']
    with catalog.connect() as db:
        r=db.execute("SELECT data_json FROM walmart_listing_metadata WHERE kind='spec'").fetchone()
        shape=json.loads(r[0]);shape['description']='new official schema'
        db.execute("UPDATE walmart_listing_metadata SET data_json=? WHERE kind='spec'",(json.dumps(shape),))
    assert start_job(catalog,publisher,b['id'],ai=ai,schedule=False)['id']!=job['id']


def test_ai_failure_keeps_source_mapping_and_conditional_requirements(publishing):
    c,catalog,fake,settings,b,base,remote=publishing
    # Same SKU source facts, independently known and available before generation.
    doc=c.get(base).json()
    for row in doc['rows']:
        row['visible']['isProp65WarningRequired']='Yes'
    assert c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).status_code==200
    class Offline(AI):
        def query(self,purpose,payload):
            raise ListingAIError('AI 请求失败（HTTP 503）')
    # Type is exact-matched from source; missing ordinary dimensions will invoke AI.
    ai=Offline();job=start_job(catalog,c.app.state.walmart_publisher,b['id'],ai=ai,schedule=False)
    run_job(catalog,c.app.state.walmart_publisher,job['id'],ai)
    current=c.get(base).json();status=c.get(base+'/generation').json()['job']
    assert status['state']=='complete'
    for old,row in zip(doc['rows'],current['rows']):
        assert row['visible']['productName']==old['visible']['productName']
        assert row['product_type']==PT and row['orderable']['price']==old['orderable']['price']
        assert 'prop65WarningText' in current['requirements'][row['platform_sku']]['visible']
        assert status['report']['rows'][row['platform_sku']]['state']=='review'
    assert not remote.posts and not remote.stock


def test_single_variant_facts_never_borrow_another_child():
    item={'category':'Chairs'}
    source={'source':'Amazon','brand':'OldBrand','variants':[
        {'sku':'A','source_facts':{'color':'red'}},
        {'sku':'B','source_facts':{'color':'blue'}}]}
    facts=source_facts(source,item,{'sku':'B','content':{'copy':{'title':'Blue chair'}}})
    assert facts['verified_source']=={'color':'blue'}
    assert 'red' not in json.dumps(facts)
