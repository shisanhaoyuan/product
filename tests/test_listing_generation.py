import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from catalog.listing_ai_client import ListingAIClient, ListingAIError
from catalog.listing_generation import choose_type, fill_row, required_keys, mapped_value, source_facts, validate_generated, fact_reference_exists, summarize_generation_issues
from catalog.listing_generation_jobs import start_job, run_job, public_job
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
    assert not issues
    assert result['visible']['isProp65WarningRequired']=='No'
    assert audit['visible.isProp65WarningRequired']['kind']=='user_rule'
    assert not ai.calls  # Skill defaults execute locally, without fabricated AI evidence.
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
    assert result['orderable']==row['orderable'] and result['source_sku']==row['source_sku']
    assert result['visible']['assembledProductHeight']=={'measure':22,'unit':'in'}
    assert any('SKU' in i['message'] for i in issues)
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


@pytest.mark.parametrize('ref,valid',[
    ('content.copy',True),('content.copy.title',True),('content.copy.features',True),
    ('content.copy.features.0',True),('content.attributes.Use Case',True),
    ('content.attributes.Name.With.Dot',True),('content.attributes',True),
    ('detail.customList.0.value',True),('content.copy.empty',False),
    ('content.copy.features.2',False),('content.copy.features.-1',False),
    ('content.copy.fake',False),('content.copy.title.fake',False),
    ('content.attributes.use case',False),('other_sku.title',False),
    (None,False),([],False),('category',False),
])
def test_fact_reference_resolves_only_real_nonempty_paths(ref,valid):
    facts={'content.copy':{'title':'Sectional Sofa','features':['Modular sofa'],'empty':'<p> </p>'},
           'content.attributes':{'Use Case':'Primary Living Space','Name.With.Dot':'Actual value'},
           'detail.customList':[{'value':False}],'category':''}
    assert fact_reference_exists(facts,ref)==valid


def test_sectional_sofa_nested_refs_correct_outdoor_type_without_identity_changes():
    row=listing();row['product_type']='Outdoor Sofas'
    before=copy.deepcopy(row)
    item={'category':'Furniture>>Primary Living Space>>Sofas'}
    facts={'category':item['category'],
           'content.copy':{'title':'103 inch L Shaped Cloud Sectional Sofa','features':['Modular Sectional Sofa']},
           'content.attributes':{'Use Case':'Primary Living Space'}}
    types=[dict(name=pt,category='Furniture',group=group) for pt,group in [
        ('Outdoor Sofas','Outdoor Seating Furniture'),('Sofas & Couches','Indoor Seating Furniture'),
        ('Sectional Sofas','Indoor Seating Furniture')]]
    class Nested(AI):
        def query(self,purpose,payload):
            return dict(source_sku='W1',product_type='Sectional Sofas',confidence=.99,
                refs=['content.copy.title','content.copy.features','content.attributes.Use Case','category'],
                reason='标题和卖点说明为室内 L 形组合沙发')
    pt,audit=choose_type(row,item,facts,types,Nested())
    assert pt=='Sectional Sofas' and audit['refs'][0]=='content.copy.title'
    assert row==before
    facts['content.copy']['title']=''
    with pytest.raises(ListingAIError,match='依据引用不存在或内容为空'):
        choose_type(row,item,facts,types,Nested())


def test_required_attributes_accept_real_leaf_refs_and_reject_missing_leaf():
    class Nested(AI):
        def query(self,purpose,payload):
            return dict(source_sku='W1',actions=[dict(field='visible.assembledProductHeight',
                value={'measure':32.5,'unit':'in'},kind='source',confidence=.99,
                refs=['content.attributes.Height'],reason='同 SKU 高度明确为32.5英寸')])
    facts={'content.attributes':{'Height':'32.5 in'}}
    audit={};row,issues=fill_row(listing(),spec(),facts,Nested(),audit)
    assert row['visible']['assembledProductHeight']=={'measure':32.5,'unit':'in'}
    assert audit['visible.assembledProductHeight']['kind']=='source'
    audit={}
    row,issues=fill_row(listing(),spec(),{'content.attributes':{'Height':''}},Nested(),audit)
    assert row['visible']['assembledProductHeight']=={'measure':22,'unit':'in'}
    assert audit['visible.assembledProductHeight']['kind']=='user_rule'
    assert 'confidence' not in audit['visible.assembledProductHeight']


def test_category_failure_points_to_type_even_with_existing_wrong_type(publishing,monkeypatch):
    c,catalog,_,_,b,base,_=publishing
    before=c.get(base).json()
    for row in before['rows']:
        row['product_type']='Outdoor Sofas'
    assert c.patch(base,json={k:before[k] for k in ('revision','version','rows')}).status_code==200
    def fail(*args):
        raise ListingAIError('AI 类目依据引用不存在或内容为空，未采用结果')
    monkeypatch.setattr('catalog.listing_generation_jobs.choose_type',fail)
    ai=AI();job=start_job(catalog,c.app.state.walmart_publisher,b['id'],ai=ai,schedule=False)
    run_job(catalog,c.app.state.walmart_publisher,job['id'],ai)
    report=c.get(base+'/generation').json()['job']
    assert report['state']=='complete'
    assert all(r['issues'][0]['path']=='product_type' for r in report['report']['rows'].values())
    assert c.get(base).json()['rows']==before['rows']


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
        assert status['report']['rows'][row['platform_sku']]['fields']['visible.isProp65WarningRequired']['kind']=='user_rule'
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


def test_skill_defaults_persisted_readback_cleanup_audit_and_repeat_noop(publishing):
    from catalog.walmart_listing_schema import sections
    c,catalog,_,_,b,base,remote=publishing
    ready(publishing)
    props=sections(remote.schema,PT)['visible']['properties']
    props['ageGroup']={'type':'array','items':{'type':'string','enum':['Adult','Child']}}
    props['has_written_warranty']={'type':'string','enum':['No','Yes - Warranty Text']}
    props['netContent']={'type':'object','properties':{
        'unit':{'type':'string','enum':['Case']},'measure':{'type':'number'}},'required':['unit','measure']}
    doc=c.get(base).json()
    for index,row in enumerate(doc['rows']):
        row['visible']['oldCategoryField']=['Prior value '+str(index)]
        row['visible']['netContent']={'unit':'Each','measure':index+3}
    assert c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).status_code==200
    # ready() fetched the earlier schema; force retrieval of the new official fixture.
    with catalog.connect() as db:
        db.execute("DELETE FROM walmart_listing_metadata WHERE kind='spec'")
    ai=AI();publisher=c.app.state.walmart_publisher
    job=start_job(catalog,publisher,b['id'],ai=ai,schedule=False)
    run_job(catalog,publisher,job['id'],ai)
    saved=c.get(base).json()
    report=c.get(base+'/generation').json()['job']
    for index,row in enumerate(saved['rows']):
        sku=row['platform_sku'];fields=report['report']['rows'][sku]['fields']
        assert row['visible']['ageGroup']==['Adult']
        assert row['visible']['has_written_warranty']=='No'
        assert row['visible']['netContent']=={'unit':'Case','measure':index+3}
        assert 'oldCategoryField' not in row['visible']
        assert fields['visible.oldCategoryField']['previous_value']==['Prior value '+str(index)]
        assert fields['visible.netContent']['kind']=='user_rule'
        assert row['orderable']==doc['rows'][index]['orderable']
        for key in ('source_sku','platform_sku','identification'):
            assert row[key]==doc['rows'][index][key]
    assert all(r['state']=='complete' for r in report['report']['rows'].values()),report
    assert start_job(catalog,publisher,b['id'],ai=ai,schedule=False)['id']==job['id']
    assert c.get(base).json()['revision']==saved['revision']
    assert not remote.posts and not remote.stock


def test_same_field_reports_are_grouped_without_losing_original_reasons_or_audit():
    issues=[
        dict(path='visible.bedSize',message='未提供睡眠区域尺寸'),
        dict(path='visible.seat_back_cushion_style',message='来源未说明靠背软垫结构'),
        dict(path='visible.bedSize',message='没有标准床型依据'),
        dict(path='visible.seat_back_cushion_style',message='无法确定软垫固定方式'),
        dict(path='identification',message='EAN 编码长度或格式错误，请填写真实编码并保留前导零'),
        dict(path='MPItem.0.Orderable.productIdentifiers.productId',message='商品编码内容不足，至少需要 1 个字符'),
        dict(path='MPItem.0.Visible.Sectional Sofas.bedSize',message='缺少必填项：床尺寸'),
        dict(path='MPItem.0.Visible.Sectional Sofas.seat_back_cushion_style',message='缺少必填项：靠背软垫款式'),
    ]
    report={'rows':{'MY21TESTAB':{'state':'review','issues':issues,'fields':{}}}}
    raw=json.dumps(report,ensure_ascii=False)
    stored=dict(id='job',batch_id='b',state='complete',revision=1,message='',started_at=0,updated_at=0,report_json=raw)
    result=public_job(stored)['report']['rows']['MY21TESTAB']
    assert len(result['issues'])==3 and result['state']=='review'
    grouped={v['path']:v for v in result['issues']}
    assert grouped['visible.bedSize']['message']=='没有标准床型依据'
    assert grouped['visible.seat_back_cushion_style']['message']=='无法确定软垫固定方式'
    assert grouped['identification']['message'].startswith('EAN 编码长度或格式错误')
    assert sum(len(v['details']) for v in result['issues'])==len(issues)
    assert stored['report_json']==raw
    assert json.loads(stored['report_json'])==report


def test_issue_grouping_does_not_merge_different_fields_or_global_failures():
    issues=[dict(path='',message='来源过期'),dict(path='',message='AI 失败'),
        dict(path='visible.keyFeatures.0',message='第一条无效'),
        dict(path='visible.keyFeatures.1',message='第二条无效'),
        dict(path='orderable.ShippingWeight',message='重量缺失')]
    assert len(summarize_generation_issues(issues))==5


def test_unresolved_style_only_queried_once_not_arbitrarily_defaulted():
    from catalog.walmart_listing_schema import sections
    shape=spec();visible=sections(shape,PT)['visible']
    for key,options in [('bedSize',['Queen','Non-Standard']),('seat_back_cushion_style',['Cushion Back','Loose Back'])]:
        visible['properties'][key]={'type':'string','enum':options}
        visible['required'].append(key)
    class Missing(AI):
        def query(self,purpose,payload):
            result=super().query(purpose,payload)
            result['missing']=[dict(field=field,reason='同 SKU 未提供此结构，无法合理估算')
                               for field in payload['fields'] if field.startswith('visible.') and field!='visible.assembledProductHeight']
            return result
    ai=Missing();row,issues=fill_row(listing(),shape,{'content.copy':{'title':'Sofa'}},ai,{})
    assert len(ai.calls)==1
    assert 'bedSize' not in row['visible'] and 'seat_back_cushion_style' not in row['visible']
    assert len([i for i in issues if i['path']=='visible.bedSize'])==1


def test_later_pass_analyzes_new_conditional_fields_without_requerying_old_fields():
    from catalog.walmart_listing_schema import sections
    shape=spec();visible=sections(shape,PT)['visible']
    visible['properties'].update(color={'type':'string','enum':['Black','White']},finish={'type':'string','enum':['Matte','Glossy']})
    visible['required'].append('color')
    visible['allOf'].append({'if':{'required':['color'],'properties':{'color':{'const':'Black'}}},
                             'then':{'required':['finish']}})
    class Conditional(AI):
        def query(self,purpose,payload):
            result=super().query(purpose,payload)
            for field,value in [('visible.color','Black'),('visible.finish','Matte')]:
                if field in payload['fields']:
                    result['actions'].append(dict(field=field,value=value,kind='estimate',
                        confidence=.6,refs=['content.copy.title'],reason='当前 SKU 上下文估算'))
            return result
    ai=Conditional();row,issues=fill_row(listing(),shape,{'content.copy':{'title':'Black chair'}},ai,{})
    assert len(ai.calls)==2
    assert list(ai.calls[1][1]['fields'])==['visible.finish']
    assert row['visible']['finish']=='Matte' and not issues
