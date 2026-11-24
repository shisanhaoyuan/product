import copy

import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.listing_ai_client import ListingAIError
from catalog.listing_content import normalize, split_single_feature
from catalog.listing_generation import fill_row
from catalog.listing_generation_jobs import start_job, run_job
from catalog.walmart_listing_schema import sections
from test_listing_generation import AI, listing
from test_saleyee import actual_quote_fixture, settings
from test_prelisting import inputs, create
from test_walmart_accounts import vault, Remote, bind
from test_walmart_publishing import spec, PT, PublisherRemote


DESCRIPTION = ('<p>A dining chair brings seating into the dining area with a coordinated beige appearance.</p>'
               '<p>The armless profile keeps the overall silhouette open, while the upholstered design '
               'adds a fabric element to the room.</p>')
FEATURES = ['Beige upholstery for a neutral look.', 'Armless dining chair design.', 'Casters beneath the chair.']
PACKED_FEATURES = ('【Key Features】 Student and Office Essential: This adjustable computer desk provides a workspace for studying or working. '
                   'Adjustable Height: The adjustable mechanism allows users to change the desk height. '
                   'Multi-functional Design: The desk has 3 USB ports and 2 AC outlets. '
                   'Black Wood Grain: The desk has a black wood grain finish.')
FACTS = {'content.copy': {'title': 'Linen Upholstered Armless Dining Chair with Casters, BEIGE',
                         'description': '', 'features': []}, 'category': 'Dining Chair'}


class CopyAI(AI):
    def query(self, purpose, payload):
        if purpose != 'listing_copy':
            return super().query(purpose, payload)
        self.calls.append((purpose, copy.deepcopy(payload)))
        return dict(source_sku=payload['source_sku'], copy={
            path.split('.')[1]: dict(value=DESCRIPTION if path.endswith('shortDescription') else FEATURES,
                refs=['content.copy.title'], reason='根据本商品标题中的款式、颜色、软包及脚轮信息编写')
            for path in payload['fields']})


def empty_copy():
    row=listing(); row['visible'].update(shortDescription='', keyFeatures=[])
    schema=spec()
    sections(schema,PT)['visible']['properties']['keyFeatures']['minItems']=3
    return row,schema


def test_composes_from_title_without_supplier_copy_or_attribute_confidence():
    row,schema=empty_copy();before=copy.deepcopy(row);facts=copy.deepcopy(FACTS);audit={};ai=CopyAI()
    result,issues=fill_row(row,schema,facts,ai,audit)
    assert result['visible']['keyFeatures']==FEATURES
    assert 'coordinated beige appearance' in result['visible']['shortDescription']
    assert not any(i['path'] in ('visible.shortDescription','visible.keyFeatures') for i in issues)
    for field in ('shortDescription','keyFeatures'):
        assert audit['visible.'+field]['kind']=='ai_composed'
        assert 'confidence' not in audit['visible.'+field]
        assert audit['visible.'+field]['refs']==['content.copy.title']
    assert len([p for p in ai.calls if p[0]=='listing_copy'])==1
    assert all(not {'visible.shortDescription','visible.keyFeatures'} & set(p['fields'])
               for purpose,p in ai.calls if purpose=='required_attributes')
    assert result['identification']==before['identification'] and result['orderable']['price']==10
    assert row==before and facts==FACTS
    repeated,_=fill_row(result,schema,facts,ai,{})
    assert repeated==result
    assert len([p for p in ai.calls if p[0]=='listing_copy'])==1


@pytest.mark.parametrize('existing', ['shortDescription','keyFeatures'])
def test_only_fills_missing_field_and_preserves_existing_copy(existing):
    row,schema=empty_copy()
    row['visible'][existing]=('<p><strong>Features:</strong></p>\n<p>1. Keep this manually written introduction.</p>'
                              if existing=='shortDescription' else FEATURES)
    before=copy.deepcopy(row['visible'][existing]);ai=CopyAI()
    result,_=fill_row(row,schema,FACTS,ai,{})
    assert result['visible'][existing]==before
    call=next(p for purpose,p in ai.calls if purpose=='listing_copy')
    assert 'visible.'+existing not in call['fields']
    assert result['visible']['shortDescription'] and result['visible']['keyFeatures']


@pytest.mark.parametrize('failure', ['wrong_sku','empty_ref','other_field','short_features','blank_html','transport'])
def test_rejects_invalid_copy_without_partial_writes(failure):
    class Bad(CopyAI):
        def query(self,purpose,payload):
            proposal=super().query(purpose,payload)
            if purpose!='listing_copy': return proposal
            if failure=='transport': raise ListingAIError('AI 请求失败（HTTP 503）')
            if failure=='wrong_sku': proposal['source_sku']='OTHER'
            if failure=='empty_ref': proposal['copy']['shortDescription']['refs']=['content.copy.description']
            if failure=='other_field': proposal['copy']['price']={'value':1}
            if failure=='short_features': proposal['copy']['keyFeatures']['value']=['one']
            if failure=='blank_html': proposal['copy']['shortDescription']['value']='<p>&nbsp;</p>'
            return proposal
    row,schema=empty_copy();ai=Bad();audit={}
    result,issues=fill_row(row,schema,FACTS,ai,audit)
    assert not result['visible'].get('shortDescription') and not result['visible'].get('keyFeatures')
    assert result['orderable']['price']==10
    assert not any(v.get('kind')=='ai_composed' for v in audit.values())
    assert any(i['path']=='visible.shortDescription' for i in issues)
    assert len([p for p in ai.calls if p[0]=='listing_copy'])==(1 if failure=='transport' else 2)


def test_rewrites_duplicate_output_then_checks_actual_normalized_content():
    class Retry(CopyAI):
        def query(self,purpose,payload):
            result=super().query(purpose,payload)
            if purpose=='listing_copy' and not payload['feedback']:
                result['copy']['shortDescription']['value']=' '.join(FEATURES)
            return result
    row,schema=empty_copy();ai=Retry()
    result,_=fill_row(row,schema,FACTS,ai,{})
    calls=[p for purpose,p in ai.calls if purpose=='listing_copy']
    assert len(calls)==2 and '清理重复内容后' in calls[1]['feedback'][0]['message']
    assert result['visible']['shortDescription'] and len(result['visible']['keyFeatures'])==3
    saved=copy.deepcopy(result['visible']);normalize(saved)
    assert saved==result['visible']


def test_html_empty_source_does_not_erase_saved_ai_description():
    row,schema=empty_copy();facts=copy.deepcopy(FACTS)
    facts['content.copy']['description']='<p>&nbsp;</p>'
    ai=CopyAI();result,_=fill_row(row,schema,facts,ai,{})
    again,_=fill_row(result,schema,facts,ai,{})
    assert again==result and len([p for p in ai.calls if p[0]=='listing_copy'])==1


def test_missing_description_with_basic_attributes_still_gets_ai_prose():
    row,schema=empty_copy();facts=copy.deepcopy(FACTS)
    facts['content.attributes']={'Color':'Beige','Material':'Linen'}
    ai=CopyAI();audit={}
    result,_=fill_row(row,schema,facts,ai,audit)
    assert 'coordinated beige appearance' in result['visible']['shortDescription']
    assert audit['visible.shortDescription']['kind']=='ai_composed'
    assert any('visible.shortDescription' in p['fields'] for purpose,p in ai.calls if purpose=='listing_copy')
    repeated,_=fill_row(result,schema,facts,ai,{})
    assert repeated==result
    assert repeated['visible']['shortDescription'].count('coordinated beige appearance')==1
    assert len([p for p in ai.calls if p[0]=='listing_copy'])==1


def test_description_minimum_word_count_is_checked():
    row,schema=empty_copy();ai=CopyAI()
    sections(schema,PT)['visible']['properties']['shortDescription']['comments']='@minimumWordCount=60'
    result,issues=fill_row(row,schema,FACTS,ai,{})
    assert not result['visible'].get('shortDescription')
    assert any('60 个英文单词' in i['message'] for i in issues)


@pytest.mark.parametrize('minimum',[2,3,4])
def test_skill_splits_one_packed_feature_to_actual_schema_without_losing_facts(minimum):
    visible={'keyFeatures':[PACKED_FEATURES]}
    definition={'type':'array','minItems':minimum,'maxItems':minimum,
                'items':{'type':'string','minLength':1,'maxLength':1000}}
    assert split_single_feature(visible,definition)
    assert len(visible['keyFeatures']) == minimum
    assert ' '.join(visible['keyFeatures']) == PACKED_FEATURES.removeprefix('【Key Features】 ')
    before=copy.deepcopy(visible)
    assert not split_single_feature(visible,definition) and visible==before


def test_split_preserves_decimal_units_abbreviations_and_expands_for_item_limit():
    source='U.S. design with a 24.5 in. wide desktop. The frame weighs 3.25 lb. The desk has 2 AC outlets.'
    visible={'keyFeatures':[source]}
    definition={'type':'array','minItems':1,'maxItems':3,'items':{'type':'string','maxLength':50}}
    assert split_single_feature(visible,definition)
    assert visible['keyFeatures']==['U.S. design with a 24.5 in. wide desktop.',
                                    'The frame weighs 3.25 lb.', 'The desk has 2 AC outlets.']
    assert ' '.join(visible['keyFeatures'])==source


def test_split_can_use_independent_semicolon_and_parallel_clauses():
    definition={'type':'array','minItems':3,'items':{'type':'string','minLength':1}}
    for source in ('The desk has USB ports; the frame is steel; the top has a black finish.',
                   'The desk has USB ports, and the frame is steel, and it has a black top.'):
        visible={'keyFeatures':[source]}
        assert split_single_feature(visible,definition)
        assert len(visible['keyFeatures'])==3


@pytest.mark.parametrize('features',[
    ['One short fact.'], ['Black; White; Blue'], ['Soft fabric.', 'Two seats.'],
    ['Has steel frame.','Has USB ports.','Black finish.'],
])
def test_split_keeps_multiple_points_or_insufficient_evidence_unchanged(features):
    visible={'keyFeatures':copy.deepcopy(features)}
    definition={'type':'array','minItems':3,'items':{'type':'string','minLength':1}}
    assert not split_single_feature(visible,definition)
    assert visible['keyFeatures']==features


def test_existing_single_long_feature_is_filled_locally_without_copy_ai():
    row,schema=empty_copy();row['visible'].update(shortDescription='An existing introduction.',keyFeatures=[PACKED_FEATURES])
    ai=CopyAI();audit={}
    result,issues=fill_row(row,schema,FACTS,ai,audit)
    assert len(result['visible']['keyFeatures'])==3
    assert ' '.join(result['visible']['keyFeatures'])==PACKED_FEATURES.removeprefix('【Key Features】 ')
    assert audit['visible.keyFeatures']['kind']=='user_rule'
    assert '按原 Skill' in audit['visible.keyFeatures']['reason']
    assert not any(purpose=='listing_copy' for purpose,_ in ai.calls)
    assert not any(i['path']=='visible.keyFeatures' for i in issues)
    for key in ('source_sku','platform_sku','identification'):
        assert result[key]==row[key]


def test_ai_single_feature_output_is_split_before_raw_minitems_rejection():
    class Packed(CopyAI):
        def query(self,purpose,payload):
            result=super().query(purpose,payload)
            if purpose=='listing_copy':
                result['copy']['keyFeatures']['value']=[' '.join(FEATURES)]
            return result
    row,schema=empty_copy();ai=Packed()
    result,issues=fill_row(row,schema,FACTS,ai,{})
    assert result['visible']['keyFeatures']==FEATURES
    assert len([1 for purpose,_ in ai.calls if purpose=='listing_copy'])==1
    assert not any(i['path']=='visible.keyFeatures' for i in issues)


def test_saleyee_generation_saves_reopens_without_publishing(tmp_path,settings,vault):
    import httpx
    from catalog.saleyee import sync_page, SyncInput
    from catalog.walmart_publishing_client import WalmartPublishingClient
    app=create_app(tmp_path/'copy.db');client=TestClient(app);catalog=app.state.catalog
    fake=actual_quote_fixture();fake.details[0]['GoodsDescriptionList']=[]
    fake.details[0]['ProductAttributeList']=[]
    fake.details[0]['EnName']=FACTS['content.copy']['title']
    sync_page(catalog,settings,SyncInput(),client=fake)
    batch,_=create(client,inputs(client,filters={'source':'Saleyee'},brand='SupplierBrand'))
    Remote().install(app);bind(client,batch['preview']['store']['id'])
    remote=PublisherRemote();sections(remote.schema,PT)['visible']['properties']['keyFeatures']['minItems']=3
    app.state.walmart_publisher=WalmartPublishingClient(httpx.MockTransport(remote.handler))
    base=f"/api/prelisting/batches/{batch['id']}/walmart"
    before=client.post(base+'/prepare').json()['rows'][0]
    original=catalog.export();ai=CopyAI()
    job=start_job(catalog,app.state.walmart_publisher,batch['id'],ai=ai,schedule=False)
    run_job(catalog,app.state.walmart_publisher,job['id'],ai)
    saved=client.get(base).json()['rows'][0]
    assert saved['visible']['shortDescription'] and saved['visible']['keyFeatures']==FEATURES
    assert saved['orderable']['price']==before['orderable']['price']
    for key in ('source_sku','platform_sku','listing_id','identification'): assert saved[key]==before[key]
    assert client.get(base).json()['rows'][0]==saved and catalog.export()==original
    report=client.get(base+'/generation').json()['job']
    assert report['report']['rows'][saved['platform_sku']]['fields']['visible.shortDescription']['kind']=='ai_composed'
    assert remote.posts==[] and remote.stock=={}
