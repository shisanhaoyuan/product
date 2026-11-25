"""Offline CA Global API contract tests; never contact or publish to real stores.

Localized Orderable content and Header follow the official MP_ITEM_INTL sample:
https://developer.walmart.com/file/mp/ca/bulk-item-setup.zip
This deliberately small test schema is not a production category specification.
"""
import hashlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.giga import GigaSettings, sync_page
from catalog.walmart_canada import DEFAULT_NODE, import_specs
from catalog.walmart_client import WalmartError
from catalog.walmart_publishing import read_feed
from catalog.walmart_publishing_client import WalmartPublishingClient, SubmissionUnknown
from catalog.listing_generation_jobs import start_job, run_job
from test_giga import FakeClient, settings
from test_giga_canada import canada
from test_prelisting import inputs, create
from test_walmart_accounts import vault, Remote, INPUT, CLIENT
from test_walmart_publishing import PublisherRemote, spec, PT, submission

VERSION = '3.15'


def canadian_spec():
    shape=spec()
    header={'subCategory':'furniture','sellingChannel':'marketplace','processMode':'REPLACE',
            'mart':'WALMART_CA','locale':['en','fr'],'version':VERSION,'subset':'EXTERNAL'}
    shape['properties']['MPItemFeedHeader']={'type':'object','properties':{
        k:{'enum':[v]} for k,v in header.items()},'required':list(header),'additionalProperties':False}
    item=shape['properties']['MPItem']['items']['properties']
    visible=item['Visible']['properties'][PT]
    visible.pop('allOf')
    for key in ('assembledProductHeight','isProp65WarningRequired','prop65WarningText'):
        visible['properties'].pop(key)
        if key in visible['required']:visible['required'].remove(key)
    for key in ('productName','brand','shortDescription','keyFeatures','mainImageUrl'):
        field=visible['properties'].pop(key)
        if key in ('productName','brand','shortDescription'):
            field={'type':'object','properties':{'en':field},'required':['en'],'additionalProperties':False}
        elif key=='keyFeatures':
            field['items']={'type':'object','properties':{'en':field['items']},'required':['en'],'additionalProperties':False}
        item['Orderable']['properties'][key]=field
        visible['required'].remove(key)
        item['Orderable']['required'].append(key)
    visible['properties']['color']={'type':'array','items':{'type':'object','properties':{
        'en':{'type':'string','enum':['Brown','White']}},'required':['en'],'additionalProperties':False}}
    visible['required'].append('color')
    return shape


class CanadaRemote(PublisherRemote):
    def __init__(self):
        super().__init__();self.node=DEFAULT_NODE

    def handler(self,request):
        assert request.headers['WM_MARKET']=='ca'
        assert request.headers['WM_GLOBAL_VERSION']=='3.1'
        assert request.url.path not in ('/v3/settings/partnerprofile','/v3/items/taxonomy','/v3/items/spec',
                                        '/v3/settings/shipping/shipnodes')
        if request.url.path=='/v3/feeds' and request.method=='POST':
            assert request.url.params['feedType']=='MP_ITEM_INTL'
        if request.url.path=='/v3/inventory':
            assert 'shipNode' not in request.url.params
            self.calls.append(request)
            sku=request.url.params['sku']
            if request.method=='PUT':
                data=json.loads(request.content);assert data['sku']==sku
                self.stock[sku]=data['quantity']['amount']
            return httpx.Response(200,json={'sku':sku,'quantity':{'unit':'EACH','amount':self.stock.get(sku,0)}})
        response=super().handler(request)
        if request.url.path=='/v3/feeds' and request.method=='GET':
            body=response.json()
            for feed in body['results']['feed']:
                feed['feedType']='MP_ITEM_INTL';feed.pop('partnerId')
            return httpx.Response(200,json=body)
        return response


@pytest.fixture
def ca_publishing(tmp_path, canada, vault):
    app=create_app(tmp_path/'ca-publish.db');c=TestClient(app)
    fake=FakeClient(currency='CAD')
    for s in fake.stock:s['sellerInventoryInfo']['sellerAvailableInventory']=25
    for d,upc in zip(fake.detail,['012345678905','012345678912']):d['upc']=upc
    sync_page(app.state.catalog,canada,1,client=fake)
    sid=inputs(c)['store_id']
    Remote().install(app)
    r=c.post('/api/walmart/accounts/preview',json={'store_id':sid,'marketplace':'CA',**INPUT})
    assert r.status_code==200,r.text
    assert c.post(f'/api/walmart/accounts/{sid}/bind',json={'verification_id':r.json()['verification_id']}).status_code==200
    b,_=create(c,inputs(c,filters={'source':'GigaB2B_CA'}))
    remote=CanadaRemote()
    app.state.walmart_publisher=WalmartPublishingClient(httpx.MockTransport(remote.handler))
    base=f"/api/prelisting/batches/{b['id']}/walmart"
    assert c.post(base+'/prepare').status_code==200
    return c,app.state.catalog,b,base,remote


def ready_ca(env):
    c,_,_,base,_=env
    r=c.post(base+'/canada/specifications',json=canadian_spec())
    assert r.status_code==200,r.text
    doc=r.json()
    assert doc['canada_specs_ready'] and doc['version']==VERSION
    assert c.post(base+'/taxonomy').json()['items'][0]['name']==PT
    assert c.post(base+'/nodes').json()['items'][0]['id']==DEFAULT_NODE
    for row in doc['rows']:
        row['product_type']=PT;row['visible']['color']=['Brown']
        row['orderable']['ShippingWeight']=10
        assert c.post(base+'/spec',json=row).status_code==200
    r=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')})
    assert r.status_code==200,r.text
    r=c.post(base+'/validate',json={'revision':r.json()['revision']})
    assert r.status_code==200,r.text
    doc=r.json()
    assert doc['validation']['valid_count']==len(doc['rows']),doc['validation']
    return doc


def test_ca_prelisting_cad_validation_submit_review_and_retry(ca_publishing):
    c,catalog,b,base,remote=ca_publishing
    assert b['preview']['store']['marketplace']=='CA' and b['preview']['store']['currency']=='CAD'
    before=catalog.export()
    doc=ready_ca(ca_publishing)
    assert doc['marketplace']=='CA' and doc['currency']=='CAD'
    assert not any(r.url.path=='/v3/settings/partnerprofile' for r in remote.calls)
    request=submission(doc)
    r=c.post(base+'/submit',json=request);assert r.status_code==200,r.text
    attempt=r.json()
    payload=remote.feeds[attempt['feed_id']]
    assert payload['MPItemFeedHeader']['mart']=='WALMART_CA'
    assert payload['MPItemFeedHeader']['subCategory']=='furniture'
    assert payload['MPItem'][0]['Orderable']['productName']['en'].startswith('NewBrand')
    assert payload['MPItem'][0]['Visible'][PT]['color']==[{'en':'Brown'}]
    assert payload['MPItem'][0]['Orderable']['price']==float(b['preview']['items'][0]['variants'][0]['price'])
    assert c.post(base+'/submit',json=request).status_code==200
    assert len(remote.posts)==1
    failed=doc['rows'][0]['platform_sku'];remote.item_status[failed]='DATA_ERROR'
    r=c.post(base+f"/attempts/{attempt['id']}/refresh");assert r.status_code==200,r.text
    results=r.json()['results']
    assert results[failed]['status']=='failed' and results[failed]['errors'][0]['original']=='Missing required color'
    good=doc['rows'][1]['platform_sku']
    assert results[good]['status']=='published' and results[good]['stock_verified']
    doc=c.get(base).json();doc['rows'][0]['visible']['color']=['White']
    r=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')});assert r.status_code==200,r.text
    doc=c.post(base+'/validate',json={'revision':r.json()['revision']}).json()
    r=c.post(base+'/submit',json=submission(doc,skus=[failed]));assert r.status_code==200,r.text
    assert len(remote.posts)==2
    assert len(remote.feeds[r.json()['feed_id']]['MPItem'])==1
    assert catalog.export()==before


def test_ca_requires_real_spec_and_checks_canonical_localized_fields(ca_publishing):
    c,_,b,base,remote=ca_publishing
    taxonomy=c.post(base+'/taxonomy')
    assert taxonomy.status_code==200 and len(taxonomy.json()['items'])==80
    doc=c.get(base).json();assert doc['canada_specs_ready'] and doc['version']=='3.16'
    assert doc['canada_specification']['source']['kind']=='bundled_official'
    for raw in ({'MPItem':[]},spec()):
        assert c.post(base+'/canada/specifications',json=raw).status_code==422
    doc=ready_ca(ca_publishing)
    # Field-level validation must retain the Canadian nested language/enum rules.
    doc['rows'][0]['visible']['color']=['Invalid color']
    r=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')});assert r.status_code==200
    report=c.post(base+'/validate',json={'revision':r.json()['revision']}).json()['validation']
    assert not report['rows'][doc['rows'][0]['platform_sku']]['valid']
    assert not remote.posts
    sync_page(c.app.state.catalog,GigaSettings(),1,client=FakeClient())
    # Bound Canadian stores cannot accept US-priced sources, even with matching SKU.
    assert c.post('/api/prelisting/preview',json=inputs(c,filters={'source':'GigaB2B'})).status_code==409


def test_ca_spec_import_rejects_external_refs_and_wrong_protocol():
    shape=canadian_spec();shape['properties']['MPItem']['items']['$ref']='https://untrusted.test/schema'
    with pytest.raises(WalmartError,match='外部'):import_specs(shape)
    shape=canadian_spec();shape['properties']['MPItemFeedHeader']['properties']['feedType']={'enum':['MP_ITEM']}
    with pytest.raises(WalmartError,match='MP_ITEM_INTL'):import_specs(shape)


def test_ca_identity_guard_scope_headers_and_unknown_submit():
    remote=CanadaRemote();client=WalmartPublishingClient(httpx.MockTransport(remote.handler))
    identity='oauth:'+hashlib.sha256(CLIENT.encode()).hexdigest()
    with pytest.raises(WalmartError,match='身份'):
        with client.session(INPUT,'oauth:wrong',marketplace='CA'):pass
    assert remote.calls==[]
    with client.session(INPUT,identity,marketplace='CA',writing=True) as session:
        remote.fail='timeout'
        with pytest.raises(SubmissionUnknown):session.submit({'MPItem':[]},'test')
    assert len(remote.posts)==1


def test_ca_feed_details_pagination_above_fifty():
    calls=[]
    class Pages:
        feed_page_size=50
        def feed(self,fid,offset):
            calls.append(offset)
            return {'feedId':fid,'itemsReceived':121,'itemDetails':{'itemIngestionStatus':[
                {'sku':str(i),'ingestionStatus':'SUCCESS'} for i in range(offset,min(offset+50,121))]}}
    summary,details=read_feed(Pages(),'ca-feed')
    assert calls==[0,50,100] and len(details)==121


def test_ca_generation_uses_shared_engine_and_persists_isolated_cad_data(ca_publishing,monkeypatch):
    from test_listing_generation import AI
    from test_images import picture
    c,catalog,b,base,remote=ca_publishing
    assert c.post(base+'/canada/specifications',json=canadian_spec()).status_code==200
    monkeypatch.setenv('CATALOG_PUBLIC_ORIGIN','https://catalog.example.test')
    monkeypatch.setattr('catalog.listing_vision.fetch_image',lambda url:picture())
    class CanadaAI(AI):
        def query(self,purpose,payload):
            result=super().query(purpose,payload)
            if purpose=='required_attributes' and 'visible.color' in payload['fields']:
                result['actions'].append(dict(field='visible.color',value=['Brown'],kind='estimate',
                    confidence=.7,refs=['content.copy'],reason='根据同 SKU 商品资料选择颜色'))
            return result
    before=c.get(base).json();ai=CanadaAI();publisher=c.app.state.walmart_publisher
    job=start_job(catalog,publisher,b['id'],ai=ai,schedule=False)
    run_job(catalog,publisher,job['id'],ai)
    status=c.get(base+'/generation').json()['job'];after=c.get(base).json()
    assert status['state']=='complete',status
    for old,new in zip(before['rows'],after['rows']):
        assert new['product_type']==PT and new['visible']['color']==['Brown']
        assert new['fulfillment_center_id']==DEFAULT_NODE
        assert new['identification']==old['identification']
        assert new['orderable']['price']==old['orderable']['price']
        assert new['orderable']['msrp']==old['orderable']['msrp']
    assert after['validation']['valid_count']==2,after['validation']
    assert not remote.posts and not remote.stock


def test_ca_unknown_submit_blocks_repost_and_recovers_original_feed(ca_publishing):
    c,_,_,base,remote=ca_publishing
    doc=ready_ca(ca_publishing);remote.fail='timeout'
    r=c.post(base+'/submit',json=submission(doc));assert r.status_code==200,r.text
    attempt=r.json();assert attempt['state']=='unknown'
    assert c.post(base+'/submit',json=submission(c.get(base).json())).status_code==409
    assert len(remote.posts)==1
    remote.fail=''
    r=c.post(base+f"/attempts/{attempt['id']}/reconcile",json={'feed_id':'feed-1'})
    assert r.status_code==200,r.text
    assert all(v['status']=='published' for v in r.json()['results'].values())
    assert len(remote.posts)==1


def test_ca_import_is_persistent_and_invalidates_validation(ca_publishing):
    from catalog.walmart_publishing import binding,cache_value
    c,catalog,b,base,_=ca_publishing
    doc=ready_ca(ca_publishing)
    with catalog.connect() as db:
        db.execute('UPDATE walmart_listing_metadata SET fetched_at=0')
        _,bound=binding(db,b['preview']['store']['id'])
        assert cache_value(db,bound,'ca_bundle')
        assert cache_value(db,bound,'spec',VERSION,PT)
    assert c.get(base).json()['canada_specs_ready']
    shape=canadian_spec()
    shape['properties']['MPItem']['items']['properties']['Visible']['properties'][PT]['properties']['color']['minItems']=2
    r=c.post(base+'/canada/specifications',json=shape);assert r.status_code==200,r.text
    assert not r.json()['validation']['current']
    assert c.post(base+'/submit',json=submission(doc)).status_code==409


def test_official_canadian_snapshot_and_source_mapping():
    import copy
    import gzip
    from importlib.resources import files
    from catalog.walmart_canada import official_bundle
    from catalog.walmart_listing_schema import feed_item, schema_errors, sections
    from catalog.listing_generation import fill_row, mapped_value
    from test_listing_generation import AI, listing
    bundle=official_bundle()
    assert bundle['version']=='3.16' and len(bundle['specs'])==80
    root=files('catalog').joinpath('specifications')
    manifest=json.loads(root.joinpath('walmart-ca-mp-item-intl.json').read_text())
    raw=gzip.decompress(root.joinpath(manifest['file']).read_bytes())
    assert hashlib.sha256(raw).hexdigest()==manifest['sha256']
    schema=bundle['specs']['Furniture'];before=copy.deepcopy(schema)
    shapes=sections(schema,'Furniture')
    assert 'productTaxCode' in shapes['orderable']['required']
    assert 'subCategory' not in schema['_ca']['header']  # Optional; no guessed mapping.
    weight_shape=shapes['orderable']['properties']['ShippingWeight']
    value,refs=mapped_value('ShippingWeight',weight_shape,{'detail.weight':10,'detail.weightUnit':'kg'})
    assert value=={'measure':22.046,'unit':'lb'} and refs
    row=listing();row['product_type']='Furniture';row['fulfillment_center_id']=DEFAULT_NODE
    row['orderable']['ShippingWeight']=10
    row['orderable']['productTaxCode']=2038710  # Explicit test fixture, never an application default.
    ai=AI();audit={}
    filled,issues=fill_row(row,schema,{'content.copy':{'title':'Wood chair','description':'Wood chair'}},ai,audit)
    assert filled['orderable']['ShippingWeight']=={'measure':10,'unit':'lb'}
    assert filled['orderable']['countryOfOriginAssembly']==['CN - China']
    entry,canonical=feed_item(filled,schema,'3.16','https://catalog.example.test',stock=10)
    assert entry['Orderable']['productName']=={'en':'NewBrand Chair'}
    assert schema_errors(canonical,entry,'3.16')==[]
    entry['Orderable']['ShippingWeight']['measure']=10.0001
    assert any('ShippingWeight.measure' in i['path'] for i in schema_errors(canonical,entry,'3.16'))
    entry['Orderable']['productName']={'en':''}
    assert any(i['path']=='MPItem.0.Visible.Furniture.productName' for i in schema_errors(canonical,entry,'3.16'))
    assert schema==before  # Shared cached schema must remain immutable.
    row['orderable'].pop('productTaxCode')
    filled,issues=fill_row(row,schema,{'content.copy':{'title':'Wood chair','description':'Wood chair'}},AI(),{})
    assert 'productTaxCode' not in filled['orderable']
    assert any(i['path']=='orderable.productTaxCode' for i in issues)


def test_builtin_canadian_schema_end_to_end_submission(ca_publishing):
    from catalog.listing_generation import fill_row
    from catalog.walmart_canada import official_bundle
    from test_listing_generation import AI
    c,_,_,base,remote=ca_publishing
    doc=c.get(base).json()
    assert doc['version']=='3.16'
    c.post(base+'/nodes')
    for i,row in enumerate(doc['rows']):
        row['product_type']='Furniture'
        row['orderable']['ShippingWeight']={'measure':12.345,'unit':'lb'}
        row['orderable']['productTaxCode']=2038710  # Verified fixture value supplied manually.
        row['visible']['color']=['Brown' if i==0 else 'White']
        schema=official_bundle()['specs']['Furniture']
        filled,_=fill_row(row,schema,{'content.copy':{'title':'Wood chair','description':'Wood chair'}},AI(),{})
        doc['rows'][i]=filled
        assert c.post(base+'/spec',json=filled).status_code==200
    saved=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')})
    assert saved.status_code==200,saved.text
    checked=c.post(base+'/validate',json={'revision':saved.json()['revision']})
    assert checked.status_code==200,checked.text
    doc=checked.json();assert doc['validation']['valid_count']==2,doc['validation']
    result=c.post(base+'/submit',json=submission(doc))
    assert result.status_code==200,result.text
    attempt=result.json();payload=remote.feeds[attempt['feed_id']]
    assert payload['MPItemFeedHeader']['version']=='3.16'
    assert payload['MPItemFeedHeader']['mart']=='WALMART_CA'
    assert payload['MPItem'][0]['Orderable']['ShippingWeight']=={'measure':12.345,'unit':'lb'}
    assert 'en' in payload['MPItem'][0]['Orderable']['productName']
    refreshed=c.post(base+f"/attempts/{attempt['id']}/refresh")
    assert refreshed.status_code==200,refreshed.text
    assert all(r['status']=='published' for r in refreshed.json()['results'].values())


def test_canadian_builtin_integrity_failure_is_closed(monkeypatch):
    from catalog import walmart_canada
    from pathlib import Path
    walmart_canada.official_bundle.cache_clear()
    class Package:
        def joinpath(self,name):
            if name=='specifications':return self
            if name.endswith('.json'):return self
            return Path(__file__).parent.parent/'catalog/specifications'/name
        def read_text(self):
            manifest=json.loads((Path(__file__).parent.parent/'catalog/specifications/walmart-ca-mp-item-intl.json').read_text())
            manifest['sha256']='0'*64
            return json.dumps(manifest)
    with monkeypatch.context() as patch:
        patch.setattr(walmart_canada,'files',lambda name:Package())
        with pytest.raises(WalmartError,match='完整性'):walmart_canada.official_bundle()
    walmart_canada.official_bundle.cache_clear()
    assert walmart_canada.official_bundle()['version']=='3.16'


def test_annotation_cleanup_preserves_properties_and_literals():
    from catalog.walmart_canada import clean_annotations
    schema={'type':'object','examples':'bad annotation','properties':{
        'examples':{'type':'string','enum':['a'],'examples':'bad field annotation'}},
        'enum':[{'examples':'a'}]}
    cleaned=clean_annotations(schema)
    assert 'examples' not in cleaned
    assert cleaned['properties']['examples']=={'type':'string','enum':['a']}
    assert cleaned['enum']==[{'examples':'a'}]


def test_amazon_canada_import_prelisting_uses_cad_and_canadian_schema(ca_publishing):
    from test_amazon_import import import_market, A
    c,catalog,_,_,_=ca_publishing
    lid=import_market(c,'CA','57.99')
    settings=inputs(c,filters={'source':'Amazon_CA'})
    preview=c.post('/api/prelisting/preview',json=settings)
    assert preview.status_code==200,preview.text
    plan=preview.json()
    assert plan['store']['marketplace']=='CA' and plan['store']['currency']=='CAD'
    assert len(plan['items'])==1 and plan['items'][0]['listing_id']==lid
    assert plan['items'][0]['variants'][0]['price']=='100.79'  # captured CAD 83.99 reference x 1.2
    batch,_=create(c,settings)
    base=f'/api/prelisting/batches/{batch["id"]}/walmart'
    result=c.post(base+'/prepare')
    assert result.status_code==200,result.text
    doc=result.json()
    assert doc['marketplace']=='CA' and doc['currency']=='CAD'
    assert doc['basis']['items'][0]['source']=='Amazon_CA'
    assert doc['rows'][0]['source_sku']==A
    assert c.post(base+'/taxonomy').status_code==200
