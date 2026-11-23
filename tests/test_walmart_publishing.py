import copy
from datetime import datetime, timezone
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.giga import sync_page
from catalog.prelisting import encoded
from catalog.walmart_listing_schema import DEFAULT_VERSION, LABELS, identifier_errors, normalize_spec, schema_errors, sections
from catalog.walmart_publishing_client import WalmartPublishingClient
from test_giga import settings
from test_giga_prelisting import warehouse, plan
from test_prelisting import create
from test_walmart_accounts import vault, Remote, bind, CLIENT, SECRET, TOKEN
from test_auth import team, login, ORIGIN

PT = 'Chairs'


def spec():
    scalar = lambda **x: dict(type='string', **x)
    dimensions = dict(type='object', properties={'unit': scalar(enum=['in']), 'measure': {'type':'number','minimum':0}},
                      required=['unit','measure'],additionalProperties=False)
    return {'$schema':'http://json-schema.org/draft-07/schema#','type':'object','additionalProperties':False,
        'properties':{
            'MPItemFeedHeader':{'type':'object','properties':{'businessUnit':scalar(enum=['WALMART_US']),
                'locale':scalar(enum=['en']),'version':scalar(enum=[DEFAULT_VERSION])},'required':['businessUnit','locale','version'],'additionalProperties':False},
            'MPItem':{'type':'array','minItems':1,'items':{'type':'object','properties':{
                'Orderable':{'type':'object','properties':{'sku':scalar(minLength=1),
                    'productIdentifiers':{'type':'object','properties':{'productIdType':scalar(enum=['UPC','GTIN','EAN','ISBN']),
                        'productId':scalar()},'required':['productIdType','productId'],'additionalProperties':False},
                    'price':{'type':'number','minimum':0.01,'multipleOf':0.01},'msrp':{'type':'number','multipleOf':0.01},
                    'ShippingWeight':{'type':'number','minimum':0.001},
                    'inventory':{'type':'array','items':{'type':'object','properties':{'quantity':{'type':'integer'},'fulfillmentCenterID':scalar()},'required':['quantity','fulfillmentCenterID']}}},
                    'required':['sku','productIdentifiers','price','ShippingWeight'],'additionalProperties':False},
                'Visible':{'type':'object','properties':{PT:{'type':'object','properties':{
                    'productName':scalar(minLength=1),'brand':scalar(minLength=1),'shortDescription':scalar(minLength=1),
                    'keyFeatures':{'type':'array','items':scalar(minLength=1),'minItems':1},'mainImageUrl':scalar(format='uri'),
                    'assembledProductHeight':dimensions,'isProp65WarningRequired':scalar(enum=['Yes','No']),
                    'prop65WarningText':scalar(minLength=1)},'required':['productName','brand','shortDescription','keyFeatures','mainImageUrl','assembledProductHeight','isProp65WarningRequired'],
                    'allOf':[{'if':{'properties':{'isProp65WarningRequired':{'const':'Yes'}},'required':['isProp65WarningRequired']},'then':{'required':['prop65WarningText']}}],
                    'additionalProperties':False}},'required':[PT],'additionalProperties':False}},
                'required':['Orderable','Visible'],'additionalProperties':False}}},'required':['MPItemFeedHeader','MPItem']}


class PublisherRemote:
    def __init__(self):
        self.partner='100009';self.calls=[];self.feeds={};self.item_status={};self.stock={};self.fail='';self.hook=None
        self.approved=True;self.owner_override='';self.node='node-17';self.schema=spec();self.item_override='';self.missing_sku='';self.filenames={}

    @property
    def posts(self):
        return [r for r in self.calls if r.method=='POST' and r.url.path=='/v3/feeds']

    def handler(self,request):
        self.calls.append(request)
        assert request.url.host=='marketplace.walmartapis.com' and request.url.scheme=='https'
        if request.url.path=='/v3/token':
            return httpx.Response(200,json={'access_token':TOKEN,'expires_in':900})
        assert request.headers['WM_SEC.ACCESS_TOKEN']==TOKEN
        path=request.url.path
        if path=='/v3/settings/partnerprofile':
            return httpx.Response(200,json={'partner':{'partnerId':self.partner}})
        if path=='/v3/token/detail':
            return httpx.Response(200,json={'is_valid':True,'scopes':{'item':'full_access','inventory':'full_access'}})
        if path=='/v3/items/taxonomy':
            return httpx.Response(200,json={'itemTaxonomy':[{'category':'Furniture','productTypeGroup':[
                {'productTypeGroupName':'Seating','productType':[{'productTypeName':PT}]}]}]})
        if path=='/v3/settings/shipping/shipnodes':
            return httpx.Response(200,json=[{'shipNode':self.node,'shipNodeName':'Test US Warehouse','status':'ACTIVE','nodeType':'PHYSICAL'},
                {'shipNode':'disabled','shipNodeName':'Disabled','status':'INACTIVE'}])
        if path=='/v3/items/spec':
            assert json.loads(request.content)=={'feedType':'MP_ITEM','version':DEFAULT_VERSION,'productTypes':[PT]}
            return httpx.Response(200,json={'schema':self.schema})
        if path=='/v3/items/gtin-exemption/status':
            return httpx.Response(200,json={'totalExemptions':1,'gtinExemptions':[{
                'productType':PT,'category':'Furniture','brand':'NewBrand','submissionId':'approved-123',
                'exemptionStatus':'APPROVED' if self.approved else 'PROCESSING'}]})
        if path=='/v3/feeds' and request.method=='POST':
            if self.hook:
                hook,self.hook=self.hook,None;hook()
            if self.fail=='400': return httpx.Response(400,text=SECRET)
            msg=BytesParser(policy=default).parsebytes(b'Content-Type: '+request.headers['content-type'].encode()+b'\r\n\r\n'+request.content)
            part=next(msg.iter_parts());payload=json.loads(part.get_payload(decode=True))
            fid='feed-'+str(len(self.feeds)+1)
            self.feeds[fid]=payload
            self.filenames[fid]=part.get_filename()
            if self.fail=='timeout': raise httpx.ReadTimeout(SECRET,request=request)
            return httpx.Response(200,json={'feedId':fid})
        if path=='/v3/feeds':
            fid=request.url.params['feedId']
            return httpx.Response(200,json={'results':{'feed':[{'feedId':fid,'feedType':'MP_ITEM','partnerId':self.owner_override or self.partner,'fileName':self.filenames[fid]}]}})
        if path.startswith('/v3/feeds/'):
            fid=path.split('/')[-1];payload=self.feeds[fid]
            details=[]
            for row in payload['MPItem']:
                sku=row['Orderable']['sku']
                if sku==self.missing_sku:continue
                status=self.item_status.get(sku,'SUCCESS')
                details.append({'sku':sku,'ingestionStatus':status,'itemid':'123','wpid':'test-wpid',
                    'ingestionErrors':{'ingestionError':[] if status=='SUCCESS' else [{'code':'ERR_MISSING_ATTRIBUTE','description':'Missing required color'}]}})
            return httpx.Response(200,json={'feedId':fid,'feedStatus':'PROCESSED','itemsReceived':len(payload['MPItem']),
                'itemDetails':{'itemIngestionStatus':details}})
        if path=='/v3/inventory':
            sku=request.url.params['sku'];assert request.url.params['shipNode']==self.node
            if request.method=='PUT':
                data=json.loads(request.content);assert data['sku']==sku
                self.stock[sku]=data['quantity']['amount']
            return httpx.Response(200,json={'sku':sku,'quantity':{'unit':'EACH','amount':self.stock.get(sku,0)}})
        if path.startswith('/v3/items/'):
            sku=path.split('/')[-1]
            return httpx.Response(200,json={'ItemResponse':[{'sku':self.item_override or sku,'publishedStatus':'PUBLISHED'}]})
        raise AssertionError((request.method,str(request.url)))


@pytest.fixture
def publishing(warehouse,vault):
    c,catalog,fake,settings=warehouse
    fake.detail[1]['upc']='012345678912'
    sync_page(catalog,settings,1,client=fake)
    b,_=create(c,plan(c))
    app=c.app
    binding_remote=Remote().install(app)
    bind(c,b['preview']['store']['id'])
    remote=PublisherRemote()
    app.state.walmart_publisher=WalmartPublishingClient(httpx.MockTransport(remote.handler))
    base=f"/api/prelisting/batches/{b['id']}/walmart"
    response=c.post(base+'/prepare');assert response.status_code==200,response.text
    return c,catalog,fake,settings,b,base,remote


def ready(env,**changes):
    c,_,_,_,_,base,remote=env
    doc=c.get(base).json()
    assert c.post(base+'/taxonomy').status_code==200
    assert c.post(base+'/nodes').status_code==200
    for row in doc['rows']:
        row['product_type']=PT;row['fulfillment_center_id']=remote.node
        row['visible'].update(assembledProductHeight={'unit':'in','measure':32.5},isProp65WarningRequired='No')
        row['orderable']['ShippingWeight']=10
        row.update(changes)
        r=c.post(base+'/spec',json=row);assert r.status_code==200,r.text
    r=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')});assert r.status_code==200,r.text
    doc=r.json()
    r=c.post(base+'/validate',json={'revision':doc['revision']});assert r.status_code==200,r.text
    return r.json()


def submission(doc,**changes):
    return dict(revision=doc['revision'],validation_id=doc['validation']['id'],request_key=str(uuid.uuid4()),
                skus=[r['platform_sku'] for r in doc['rows']],confirm_store_id=doc['store']['id'],
                confirm_partner_id=doc['validation']['partner_id']) | changes


def send(env,doc,**changes):
    c,*_,base,remote=env
    r=c.post(base+'/submit',json=submission(doc,**changes));assert r.status_code==200,r.text
    return r.json()


def test_document_isolation_price_stock_identity_reopen_and_idempotent_save(publishing):
    c,catalog,_,_,b,base,remote=publishing
    before=catalog.export();doc=ready(publishing)
    assert doc['validation']['valid_count']==2,doc['validation']
    assert all(r['orderable']['price']==79.73 for r in doc['rows'])
    assert all(v['stock']=='10' for i in doc['basis']['items'] for v in i['variants'])
    assert catalog.export()==before
    revision=doc['revision']
    assert c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).json()['revision']==revision
    assert c.post(base+'/prepare').json()['revision']==revision
    reopened=TestClient(create_app(catalog.path))
    assert reopened.get(base).json()['rows']==doc['rows']
    bad=copy.deepcopy(doc);bad['rows'][0]['source_sku']='W-other'
    assert c.patch(base,json={k:bad[k] for k in ('revision','version','rows')}).status_code==409
    bad=copy.deepcopy(doc);bad['rows'][0]['orderable']['sku']='evil'
    assert c.patch(base,json={k:bad[k] for k in ('revision','version','rows')}).status_code==422


def test_ean_default_preserves_existing_source_identifiers():
    from catalog.walmart_publishing import Identification, default_rows
    assert Identification().type == 'EAN'
    def row(code='', kind=''):
        preview = {'brand': 'Test', 'items': [{'listing_id': 'item', 'variants': [
            {'platform_sku': 'store-sku', 'sku': 'source-sku',
             'content': {'ean': code, 'product_id_type': kind}}]}]}
        return default_rows(preview)[0]['identification']
    assert row('', 'GTIN')['type'] == 'EAN'
    assert row()['value'] == ''
    for code, kind in [('012345678905', 'UPC'), ('9780306406157', 'EAN'), ('00012345678905', 'GTIN')]:
        assert row(code, kind)['type'] == kind
        assert row(code, kind)['value'] == code


def test_document_category_hint_available_to_existing_documents(publishing):
    c, catalog, _, _, _, base, _ = publishing
    doc = c.get(base).json()
    assert all('category' in item for item in doc['basis']['items'])
    with catalog.connect() as db:
        stored = db.execute('SELECT batch_id,basis_json FROM walmart_listing_documents').fetchone()
        basis = json.loads(stored['basis_json'])
        for item in basis['items']:
            item.pop('category', None)
        db.execute('UPDATE walmart_listing_documents SET basis_json=? WHERE batch_id=?',
                   (encoded(basis), stored['batch_id']))
    reopened = c.get(base).json()
    assert reopened['rows'] == doc['rows']
    assert [i['category'] for i in reopened['basis']['items']] == [i['category'] for i in doc['basis']['items']]
    assert all('category_hints' in item for item in reopened['basis']['items'])


def test_category_hints_preserve_actual_source_labels_and_old_document_backfill(publishing):
    from catalog.walmart_publishing import source_category_hints
    source = {'source': 'GigaB2B', 'fields': {'GIGA category': 'Furniture > Sofas'},
              'source_facts': {'giga': {'detail': {'category': 'Sofas', 'categoryPath': 'Furniture > Sofas',
                                                   'categoryCode': 10015}}}}
    assert source_category_hints(source) == ['Furniture > Sofas', 'Sofas']
    assert source_category_hints({'fields': {'dc_productType': 'Chairs'}}) == ['Chairs']
    assert source_category_hints({}) == []
    c, catalog, _, _, _, base, _ = publishing
    doc = c.get(base).json()
    with catalog.connect() as db:
        stored = db.execute('SELECT batch_id,basis_json FROM walmart_listing_documents').fetchone()
        basis = json.loads(stored['basis_json'])
        for item in basis['items']:
            item.pop('category_hints', None)
        db.execute('UPDATE walmart_listing_documents SET basis_json=? WHERE batch_id=?',
                   (encoded(basis), stored['batch_id']))
    reopened = c.get(base).json()
    assert reopened['rows'] == doc['rows']
    assert all('category_hints' in item for item in reopened['basis']['items'])
    assert c.get(base).json() == reopened


def test_schema_required_nested_conditional_enum_and_unsafe_images(publishing):
    c,_,_,_,_,base,_=publishing
    doc=ready(publishing)
    for r in doc['rows']:
        r['visible']['assembledProductHeight']={'unit':'cm'}
        r['visible']['isProp65WarningRequired']='Yes'
        r['visible']['mainImageUrl']='http://127.0.0.1/a.jpg'
    saved=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).json()
    report=c.post(base+'/validate',json={'revision':saved['revision']}).json()['validation']
    assert report['valid_count']==0
    # Fix images to ensure complete official schema validation is actually reached.
    for r in saved['rows']:r['visible']['mainImageUrl']='https://example.com/a.jpg'
    saved=c.patch(base,json={k:saved[k] for k in ('revision','version','rows')}).json()
    report=c.post(base+'/validate',json={'revision':saved['revision']}).json()['validation']
    messages=' '.join(e['message'] for r in report['rows'].values() for e in r['errors'])
    assert '数值' in messages and '请选择' in messages and '加州 65 号警告内容' in messages
    assert any(e['path'].endswith('.prop65WarningText') for r in report['rows'].values() for e in r['errors'])
    saved['validation']=report
    assert c.post(base+'/submit',json=submission(saved)).status_code==409


def test_official_feed_payload_submit_once_results_and_only_failed_retry(publishing):
    c,_,_,_,b,base,remote=publishing;doc=ready(publishing)
    payload=submission(doc);r=c.post(base+'/submit',json=payload);assert r.status_code==200,r.text
    a=r.json();assert a['feed_id']=='feed-1' and a['state']=='processing'
    assert len(remote.posts)==1
    assert c.post(base+'/submit',json=payload).json()['id']==a['id'] and len(remote.posts)==1
    assert c.post(base+'/submit',json=payload|{'skus':payload['skus'][:1]}).status_code==409
    assert c.post(base+'/submit',json=submission(doc)).status_code==409
    assert c.post(f"/api/prelisting/batches/{b['id']}/action",json={'action':'cancel'}).status_code==409
    wire=remote.feeds['feed-1']
    assert wire['MPItemFeedHeader']['version']==DEFAULT_VERSION
    assert {r['Orderable']['sku'] for r in wire['MPItem']}==set(payload['skus'])
    assert all(r['Orderable']['inventory']==[{'quantity':10,'fulfillmentCenterID':remote.node}] for r in wire['MPItem'])
    failed=doc['rows'][0]['platform_sku'];remote.item_status[failed]='DATA_ERROR'
    response=c.post(base+f"/attempts/{a['id']}/refresh");assert response.status_code==200,response.text
    statuses=response.json()['results'];assert statuses[failed]['status']=='failed'
    assert '必填' in statuses[failed]['errors'][0]['message']
    assert statuses[doc['rows'][1]['platform_sku']]['status']=='published'
    doc=c.get(base).json();doc['rows'][0]['visible']['shortDescription']='Corrected chair description'
    saved=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')});assert saved.status_code==200,saved.text
    checked=c.post(base+'/validate',json={'revision':saved.json()['revision']}).json()
    assert c.post(base+'/submit',json=submission(checked)).status_code==409 # Successful SKU cannot be resent.
    remote.item_status[failed]='SUCCESS'
    second=send(publishing,checked,skus=[failed]);assert second['feed_id']=='feed-2'
    assert len(remote.feeds['feed-2']['MPItem'])==1
    assert remote.feeds['feed-2']['MPItem'][0]['Orderable']['sku']==failed
    assert c.post(base+f"/attempts/{second['id']}/refresh").json()['results'][failed]['status']=='published'


def test_unknown_submission_requires_exact_owner_and_sku_reconciliation(publishing):
    c,_,_,_,_,base,remote=publishing;doc=ready(publishing);remote.fail='timeout'
    a=send(publishing,doc);assert a['state']=='unknown' and not a['feed_id']
    assert c.post(base+'/submit',json=submission(doc)).status_code==409
    assert len(remote.posts)==1
    remote.owner_override='other-partner'
    assert c.post(base+f"/attempts/{a['id']}/reconcile",json={'feed_id':'feed-1'}).status_code==400
    remote.owner_override='';remote.missing_sku=doc['rows'][0]['platform_sku']
    assert c.post(base+f"/attempts/{a['id']}/reconcile",json={'feed_id':'feed-1'}).status_code==409
    remote.missing_sku=''
    response=c.post(base+f"/attempts/{a['id']}/reconcile",json={'feed_id':'feed-1'})
    assert response.status_code==200,response.text
    assert response.json()['feed_id']=='feed-1' and len(remote.posts)==1


def test_source_refresh_no_double_markup_binding_and_stale_validation_guards(publishing):
    c,catalog,fake,settings,_,base,remote=publishing;doc=ready(publishing)
    assert c.post(base+'/submit',json=submission(doc,confirm_store_id=999)).status_code==409
    remote.partner='other-partner'
    assert c.post(base+'/submit',json=submission(doc)).status_code==400
    remote.partner='100009'
    fake.prices[0]['price']='70';sync_page(catalog,settings,1,client=fake)
    assert c.post(base+'/submit',json=submission(doc)).status_code==409
    for _ in range(2):
        response=c.post(base+'/refresh-source',json={'revision':doc['revision']});assert response.status_code==200,response.text
        doc=response.json()
        row=next(r for r in doc['rows'] if r['source_sku']=='W1')
        assert row['orderable']['price']==83.18 # (70+2.33)*1.15, never already marked-up price.
        assert row['orderable']['msrp']==159.99 # Preserve the existing suggestion; no forced repricing.
        assert row['visible']['assembledProductHeight']['measure']==32.5
    checked=c.post(base+'/validate',json={'revision':doc['revision']}).json()
    assert checked['validation']['valid_count']==2,checked['validation']
    with catalog.connect() as db:
        report=json.loads(db.execute('SELECT validation_json FROM walmart_listing_documents WHERE batch_id=?',(publishing[4]['id'],)).fetchone()[0]);report['at']=0
        db.execute('UPDATE walmart_listing_documents SET validation_json=? WHERE batch_id=?',(encoded(report),publishing[4]['id']))
    assert c.get(base).json()['validation']['current'] is True
    assert c.post(base+'/submit',json=submission(checked)).status_code==200
    assert len(remote.posts)==1


def test_exact_store_brand_type_gtin_exemption_and_invalid_check_digit(publishing):
    c,catalog,_,_,b,base,remote=publishing
    identity={'mode':'exemption','type':'UPC','value':'','submission_id':'approved-123'}
    doc=ready(publishing,identification=identity)
    assert doc['validation']['valid_count']==2,doc['validation']
    with catalog.connect() as db:
        report=json.loads(db.execute('SELECT validation_json FROM walmart_listing_documents WHERE batch_id=?',(b['id'],)).fetchone()[0])
    assert all('productIdentifiers' not in e['Orderable'] for e in report['payloads'].values())
    remote.approved=False
    report=c.post(base+'/validate',json={'revision':doc['revision']}).json()['validation']
    assert report['valid_count']==0
    for value,kind,valid in [('012345678905','UPC',True),('012345678904','UPC',False),('00012345678905','GTIN',True),('9780306406157','EAN',True),('0306406152','ISBN',True),('1234','UPC',False)]:
        assert bool(identifier_errors({'mode':'identifier','type':kind,'value':value})) is not valid


def test_duplicate_double_click_concurrent_submission(publishing):
    c,_,_,_,_,base,remote=publishing;doc=ready(publishing)
    started=threading.Event();release=threading.Event()
    def hook(): started.set();assert release.wait(10)
    remote.hook=hook
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(c.post,base+'/submit',json=submission(doc))
        assert started.wait(5)
        second=c.post(base+'/submit',json=submission(doc))
        assert second.status_code==409
        release.set();assert first.result().status_code==200
    assert len(remote.posts)==1


def test_real_official_sofa_schema_nested_units_precision_and_required_fields():
    schema=json.loads((Path(__file__).parent/'fixtures/walmart/sofas-mp-item-20260829.json').read_text())
    normalized=normalize_spec({'schema':schema},'Sofas & Couches',DEFAULT_VERSION)
    props=sections(normalized,'Sofas & Couches')
    assert 'assembledProductHeight' in props['visible']['required']
    assert {'unit','measure'}==set(props['visible']['properties']['assembledProductHeight']['required'])
    item={'Orderable':{'sku':'MY17ABC123','price':79.73},'Visible':{'Sofas & Couches':{'productName':'Test sofa','brand':'TestBrand','shortDescription':'A sofa'}}}
    errors=schema_errors(schema,item,DEFAULT_VERSION)
    assert any('运输重量' in e['message'] for e in errors)
    assert not any(e['path'].endswith('.price') for e in errors) # Exact decimal validation, not floating-point modulo.


def test_optional_listing_attributes_have_employee_friendly_chinese_labels():
    assert LABELS['arm_height'] == '扶手高度'
    assert LABELS['maximumLoadWeight'] == '最大承重'
    assert LABELS['product_package_dimensions_and_weight'] == '产品包装尺寸和重量'
    assert LABELS['externalProductIdentifier'] == '外部产品标识'
    assert LABELS['MustShipAlone'] == '是否必须单独运输'


def test_submission_rejection_is_retryable_and_never_exposes_secrets(publishing):
    c,_,_,_,_,base,remote=publishing;doc=ready(publishing);remote.fail='400'
    a=send(publishing,doc);assert a['state']=='rejected' and 'HTTP 400' in a['message']
    assert all(s not in json.dumps(a) for s in (SECRET,CLIENT,TOKEN))
    remote.fail=''
    doc=c.post(base+'/validate',json={'revision':doc['revision']}).json()
    second=send(publishing,doc);assert second['state']=='processing'
    assert len(remote.posts)==2


def test_wrong_owner_wrong_sku_and_incomplete_results_do_not_write_stock(publishing):
    c,_,_,_,_,base,remote=publishing;doc=ready(publishing);a=send(publishing,doc)
    url=base+f"/attempts/{a['id']}/refresh"
    remote.owner_override='other'
    assert c.post(url).status_code==400 and not remote.stock
    remote.owner_override='';remote.missing_sku=doc['rows'][0]['platform_sku']
    r=c.post(url);assert r.status_code==200,r.text
    assert r.json()['results'][remote.missing_sku]['status']=='processing'
    assert remote.missing_sku not in remote.stock
    remote.missing_sku='';remote.item_override='wrong-sku'
    r=c.post(url);assert r.status_code==200
    assert all(v['status']!='published' for v in r.json()['results'].values())


def test_current_source_stock_is_used_for_exact_sku_after_ingestion(publishing):
    c,catalog,fake,settings,_,base,remote=publishing;doc=ready(publishing);a=send(publishing,doc)
    fake.stock[0]['sellerInventoryInfo']['sellerAvailableInventory']=3
    sync_page(catalog,settings,1,client=fake)
    r=c.post(base+f"/attempts/{a['id']}/refresh");assert r.status_code==200,r.text
    first=next(r['platform_sku'] for r in doc['rows'] if r['source_sku']=='W1')
    second=next(r['platform_sku'] for r in doc['rows'] if r['source_sku']=='W2')
    # Initial request contained 10, but changed source stock must never be blindly reset to 10.
    assert remote.stock.get(first,0)==0 and remote.stock[second]==10


def test_store_cannot_be_rebound_or_modified_during_inflight_post(publishing):
    c,_,_,_,_,base,remote=publishing;doc=ready(publishing)
    def hook():
        response=c.post(f"/api/walmart/accounts/{doc['store']['id']}/unbind",json={'revision':1})
        assert response.status_code==409
        changed=copy.deepcopy(doc);changed['rows'][0]['visible']['shortDescription']='Do not change while sending'
        assert c.patch(base,json={k:changed[k] for k in ('revision','version','rows')}).status_code==409
    remote.hook=hook
    assert send(publishing,doc)['state']=='processing'


def test_editor_can_prepare_but_only_admin_can_publish_and_query(team,vault):
    admin,_=login(team);editor,_=login(team,'editor');viewer,_=login(team,'viewer')
    # Permission failures happen before looking up batch IDs or making any external call.
    prefix='/api/prelisting/batches/not-a-batch/walmart'
    payload={'revision':1,'validation_id':'a'*32,'request_key':str(uuid.uuid4()),'skus':['SKU'],
        'confirm_store_id':1,'confirm_partner_id':'100009'}
    for client in (editor,viewer):
        assert client.post(prefix+'/submit',json=payload).status_code==403
        assert client.post(prefix+'/attempts/no-attempt/refresh').status_code==403
    assert viewer.post(prefix+'/prepare').status_code==403
    assert editor.post(prefix+'/prepare').status_code==404
    assert admin.post(prefix+'/submit',json=payload,headers={'X-CSRF-Token':''}).status_code==403
    assert admin.post(prefix+'/submit',json=payload,headers={'Origin':'https://evil.example'}).status_code==403


def test_signed_local_images_only_public_after_explicit_submission(publishing,monkeypatch):
    import io
    from PIL import Image
    c,catalog,_,_,_,base,remote=publishing
    monkeypatch.setenv('CATALOG_PUBLIC_ORIGIN','https://catalog.example.test')
    doc=ready(publishing);row=doc['rows'][0]
    buffer=io.BytesIO();Image.new('RGB',(20,20),'blue').save(buffer,format='PNG')
    uploaded=c.post('/api/products/'+row['listing_id']+'/images/upload',content=buffer.getvalue(),headers={'Content-Type':'image/png'})
    assert uploaded.status_code==200,uploaded.text
    row['visible']['mainImageUrl']=uploaded.json()['url']
    saved=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).json()
    doc=c.post(base+'/validate',json={'revision':saved['revision']}).json()
    with catalog.connect() as db:
        report=json.loads(db.execute('SELECT validation_json FROM walmart_listing_documents').fetchone()[0])
    from urllib.parse import urlparse
    url=report['payloads'][row['platform_sku']]['Visible'][PT]['mainImageUrl'];path=urlparse(url).path
    assert path.startswith('/api/listing-media/') and c.get(path).status_code==404
    send(publishing,doc)
    assert c.get(path).status_code==200
    assert c.get(path[:-1]+('a' if path[-1]!='a' else 'b')).status_code==404


def test_invalid_numbers_and_sku_changes_never_enter_payload(publishing):
    c,_,_,_,_,base,remote=publishing;doc=ready(publishing)
    doc['rows'][0]['orderable']['price']=float('inf')
    # Send explicitly so httpx's standards-compliant JSON encoder doesn't reject first.
    response=c.patch(base,content=json.dumps({k:doc[k] for k in ('revision','version','rows')}),headers={'Content-Type':'application/json'})
    assert response.status_code==422 and not remote.posts


def test_old_feed_with_same_skus_cannot_resolve_unknown_request(publishing):
    c,_,_,_,_,base,remote=publishing;doc=ready(publishing);remote.fail='timeout';a=send(publishing,doc)
    remote.filenames['feed-1']='old-same-skus.json'
    assert c.post(base+f"/attempts/{a['id']}/reconcile",json={'feed_id':'feed-1'}).status_code==409


def test_required_errors_identify_each_missing_field_once():
    from jsonschema import Draft7Validator
    from catalog.walmart_listing_schema import chinese_schema_error
    errors=list(Draft7Validator({'required':['color','material','size']}).iter_errors({}))
    messages=[chinese_schema_error(e) for e in errors]
    assert [e['path'] for e in messages]==['.color','.material','.size']
    assert [e['message'] for e in messages]==['缺少必填项：颜色','缺少必填项：材质','缺少必填项：尺寸']


def test_old_attempt_cannot_write_inventory_after_new_retry_claims_sku(publishing):
    c,_,_,_,_,base,remote=publishing
    doc=ready(publishing);first=send(publishing,doc)
    target=doc['rows'][0]['platform_sku']
    remote.item_status[target]='SYSTEM_ERROR'  # A transport/system terminal failure needs no data patch.
    assert c.post(base+f"/attempts/{first['id']}/refresh").status_code==200
    current=c.get(base).json()
    checked=c.post(base+'/validate',json={'revision':current['revision']}).json()
    second=send(publishing,checked,skus=[target])
    remote.item_status[target]='SUCCESS';remote.stock[target]=0
    writes=len([r for r in remote.calls if r.method=='PUT'])
    response=c.post(base+f"/attempts/{first['id']}/refresh")
    assert response.status_code==200,response.text
    assert remote.stock[target]==0
    assert len([r for r in remote.calls if r.method=='PUT'])==writes
    assert '较新提交' in response.json()['results'][target]['stock_message']
    assert c.post(base+f"/attempts/{second['id']}/refresh").json()['results'][target]['status']=='published'
    assert remote.stock[target]==10


def test_required_upstream_reason_names_recognized_field_in_chinese():
    from catalog.walmart_listing_schema import upstream_reason
    reasons=upstream_reason([{'code':'ERR_MISSING_ATTRIBUTE','description':'Missing required color'}])
    assert reasons[0]['message']=='缺少必填资料：颜色'
    assert reasons[0]['original']=='Missing required color'


def test_missing_primary_image_is_not_translated_as_missing_attributes():
    from catalog.walmart_listing_schema import upstream_reason
    original = "This item is missing a primary image. Add a 'Main Image URL' and resubmit the item."
    reason = upstream_reason([{'description': original}])[0]
    assert '主图' in reason['message'] and '必填资料' not in reason['message']
    assert reason['original'] == original


def test_unpublished_main_image_feedback_blocks_unchanged_image_and_unrelated_edits(publishing):
    c,_,_,_,_,base,remote=publishing
    doc=ready(publishing);a=send(publishing,doc);target=doc['rows'][0]['platform_sku']
    original_handler=remote.handler
    def handler(request):
        if request.url.path=='/v3/items/'+target:
            return httpx.Response(200,json={'ItemResponse':[{'sku':target,'publishedStatus':'UNPUBLISHED',
                'unpublishedReasons':{'reason':["This item is missing a primary image. Add a 'Main Image URL' and resubmit the item."]}}]})
        return original_handler(request)
    c.app.state.walmart_publisher=WalmartPublishingClient(httpx.MockTransport(handler))
    result=c.post(base+f"/attempts/{a['id']}/refresh")
    assert result.status_code==200,result.text
    assert result.json()['results'][target]['status']=='unpublished'
    current=c.get(base).json()
    assert not current['validation']['current']
    checked=c.post(base+'/validate',json={'revision':current['revision']}).json()
    errors=checked['validation']['rows'][target]['errors']
    assert any(e['path']=='visible.mainImageUrl' and '主图' in e['message'] for e in errors)
    assert c.post(base+'/submit',json=submission(checked,skus=[target])).status_code==409
    checked['rows'][0]['orderable']['price']+=1
    saved=c.patch(base,json={k:checked[k] for k in ('revision','version','rows')}).json()
    checked=c.post(base+'/validate',json={'revision':saved['revision']}).json()
    assert not checked['validation']['rows'][target]['valid']
    checked['rows'][0]['visible']['mainImageUrl']='https://example.com/repaired-image.jpg'
    saved=c.patch(base,json={k:checked[k] for k in ('revision','version','rows')}).json()
    checked=c.post(base+'/validate',json={'revision':saved['revision']}).json()
    assert checked['validation']['rows'][target]['valid'],checked['validation']
    assert len(remote.posts)==1  # Repair and validation must never resubmit automatically.


def test_validation_before_publication_feedback_fix_requires_recheck(publishing):
    c,catalog,_,_,b,base,_=publishing
    doc=ready(publishing)
    with catalog.connect() as db:
        report=json.loads(db.execute('SELECT validation_json FROM walmart_listing_documents WHERE batch_id=?',(b['id'],)).fetchone()[0])
        report.pop('feedback_policy')
        db.execute('UPDATE walmart_listing_documents SET validation_json=? WHERE batch_id=?',(encoded(report),b['id']))
    assert not c.get(base).json()['validation']['current']
    assert c.post(base+'/submit',json=submission(doc)).status_code==409


def test_unknown_local_image_digest_cannot_be_submitted(publishing):
    c,_,_,_,_,base,_=publishing;doc=ready(publishing)
    doc['rows'][0]['visible']['mainImageUrl']='/api/products/images/assets/'+'a'*64
    saved=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).json()
    checked=c.post(base+'/validate',json={'revision':saved['revision']}).json()
    errors=checked['validation']['rows'][doc['rows'][0]['platform_sku']]['errors']
    assert any(e['message']=='本地图片不存在，请重新上传真实图片' for e in errors)
    assert c.post(base+'/submit',json=submission(checked)).status_code==409


def test_stock_changed_during_remote_get_is_rechecked_before_put(publishing):
    c,catalog,fake,settings,_,base,remote=publishing
    doc=ready(publishing);a=send(publishing,doc)
    first=next(r['platform_sku'] for r in doc['rows'] if r['source_sku']=='W1')
    original=remote.handler;changed=False
    def handler(request):
        nonlocal changed
        if not changed and request.url.path=='/v3/inventory' and request.method=='GET' and request.url.params['sku']==first:
            changed=True
            fake.stock[0]['sellerInventoryInfo']['sellerAvailableInventory']=3
            sync_page(catalog,settings,1,client=fake)
        return original(request)
    c.app.state.walmart_publisher=WalmartPublishingClient(httpx.MockTransport(handler))
    response=c.post(base+f"/attempts/{a['id']}/refresh")
    assert response.status_code==200,response.text
    assert changed and remote.stock.get(first,0)==0
    assert not any(r.method=='PUT' and r.url.params['sku']==first for r in remote.calls)


def test_removed_product_type_returns_validation_issue_even_with_exemption(publishing):
    c,catalog,_,_,_,base,_=publishing
    doc=ready(publishing,identification={'mode':'exemption','type':'UPC','value':'','submission_id':'approved-123'})
    with catalog.connect() as db:
        db.execute("UPDATE walmart_listing_metadata SET data_json='[]' WHERE kind='taxonomy'")
    response=c.post(base+'/validate',json={'revision':doc['revision']})
    assert response.status_code==200,response.text
    checked=response.json()
    assert checked['validation']['valid_count']==0
    assert all(any(e['path']=='product_type' for e in r['errors']) for r in checked['validation']['rows'].values())
    assert c.post(base+'/submit',json=submission(checked)).status_code==409


@pytest.mark.parametrize('quantity',[0,7,25])
def test_manual_target_stock_is_saved_submitted_and_used_after_ingestion(publishing,quantity):
    c,catalog,_,_,_,base,remote=publishing
    doc=ready(publishing,target_stock=quantity)
    assert doc['validation']['valid_count']==2,doc['validation']
    assert all(r['target_stock']==quantity for r in c.get(base).json()['rows'])
    assert all(v['stock']=='10' for i in doc['basis']['items'] for v in i['variants'])
    attempt=send(publishing,doc)
    wire=remote.feeds[attempt['feed_id']]
    assert all(r['Orderable']['inventory'][0]['quantity']==quantity for r in wire['MPItem'])
    result=c.post(base+f"/attempts/{attempt['id']}/refresh")
    assert result.status_code==200,result.text
    assert all(r['stock_verified'] for r in result.json()['results'].values())
    assert all(remote.stock.get(r['platform_sku'],0)==quantity for r in doc['rows'])
    locked=c.get(base).json();locked['rows'][0]['target_stock']=12
    assert c.patch(base,json={k:locked[k] for k in ('revision','version','rows')}).status_code==409


@pytest.mark.parametrize('quantity',[-1,1.5,True,'10',1000000000])
def test_invalid_manual_stock_is_rejected(publishing,quantity):
    c,_,_,_,_,base,_=publishing
    doc=c.get(base).json();doc['rows'][0]['target_stock']=quantity
    assert c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).status_code==422


def test_manual_stock_can_override_source_zero_without_masking_identity_or_price_errors():
    from catalog.walmart_publishing import source_issues
    item=dict(source='Amazon',errors=['child：库存缺失或不符合 0 / 10 规则，执行前需核验'],observed_at=datetime.now(timezone.utc).isoformat())
    variant=dict(sku='child',stock='0',price='20')
    assert source_issues(item,variant)
    assert source_issues(item,variant,target_stock=6)==[]
    assert source_issues(item,variant,target_stock=0)==[]
    item['errors'].append('供应商 SKU 与价格、库存或详情来源不一致，请重新同步')
    errors=source_issues(item,variant | {'price':None},target_stock=6)
    assert any('SKU' in e for e in errors) and any('售价' in e for e in errors)



def test_refresh_source_preserves_manual_stock_and_clearing_restores_default(publishing):
    c,_,_,_,_,base,_=publishing
    doc=ready(publishing,target_stock=23)
    refreshed=c.post(base+'/refresh-source',json={'revision':doc['revision']})
    assert refreshed.status_code==200,refreshed.text
    doc=refreshed.json()
    assert all(row['target_stock']==23 for row in doc['rows'])
    for row in doc['rows']:row['target_stock']=None
    saved=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')})
    assert saved.status_code==200,saved.text
    assert all('target_stock' not in row for row in saved.json()['rows'])
