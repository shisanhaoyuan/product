import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from catalog.app import create_app
from catalog.db import Catalog
from catalog.models import ImportBundle, Listing, Variant
from catalog.adapters.digitemperor import build_bundle, product_images, normalize


def listing(**overrides):
    return Listing.model_validate(dict(external_id='store|record|template',asin='B012345678',sku='B012345678-p',
        store='MYZ-L-21',brand='Growlume',template='template',record_id='record',
        observed_at='2026-10-23T12:00:00+00:00',evidence_kind='persisted_readback',save_succeeded=True,
        readback_verified=True,fill_complete=True,variants=[dict(sku='child-a',asin='B012345679',price='30.99',stock='0',
        copy=dict(title='child A',description='A only',features=['A feature']))],**overrides))


def bundle(items,namespace='test'):
    return ImportBundle(namespace=namespace,label='测试导入',listings=items)


def test_import_idempotency_and_snapshot(tmp_path):
    c=Catalog(tmp_path/'catalog.db');p=listing()
    assert c.ingest(bundle([p]))['created']==1
    assert c.ingest(bundle([p]))['unchanged']==1
    id=c.find()['items'][0]['id']
    newer=p.model_copy(deep=True);newer.observed_at='2026-10-24T12:00:00+00:00';newer.variants[0].stock='10'
    assert c.ingest(bundle([newer]))['updated']==1
    data=c.get(id);assert len(data['history'])==2 and data['variants'][0]['stock']=='10'
    assert c.ingest(bundle([p]))['older_skipped']==1
    assert c.get(id)['variants'][0]['stock']=='10'


@pytest.mark.parametrize('field,value',[('asin','B099999999'),('sku','other'),('store','store2'),('template','other'),('record_id','other')])
def test_identity_conflict_transaction_rolls_back(tmp_path,field,value):
    c=Catalog(tmp_path/'catalog.db');p=listing();c.ingest(bundle([p]))
    new=p.model_copy(update={'external_id':'new-record','record_id':'new-record'})
    changed=p.model_copy(update={field:value,'observed_at':'2026-10-24T12:00:00+00:00'})
    with pytest.raises(ValueError,match='身份冲突'):c.ingest(bundle([new,changed]))
    assert c.stats()['listings']==1


def test_store_listing_independence_and_sku_mapping(tmp_path):
    c=Catalog(tmp_path/'catalog.db');a=listing()
    b=a.model_copy(deep=True);b.external_id='second';b.store='MYZ-L-22';b.variants.reverse();b.variants[0].price='99.99'
    c.ingest(bundle([a,b]));assert c.stats()['products']==1 and c.stats()['listings']==2
    assert c.find(q='B012345679')['total']==2
    assert c.find(store='MYZ-L-21')['items'][0]['prices']==['30.99']
    assert c.find(store='MYZ-L-22')['items'][0]['prices']==['99.99']


def test_ambiguous_duplicates_rejected(tmp_path):
    c=Catalog(tmp_path/'catalog.db');p=listing()
    with pytest.raises(ValueError,match='重复'):c.ingest(bundle([p,p]))
    assert c.stats()['listings']==0
    data=p.model_dump();data['variants']*=2
    with pytest.raises(ValidationError,match='唯一'):Listing.model_validate(data)


def test_snapshot_cannot_claim_saved_verification():
    data=listing().model_dump();data['evidence_kind']='editor_snapshot'
    with pytest.raises(ValidationError,match='快照'):Listing.model_validate(data)


def test_snapshot_never_replaces_persisted_readback(tmp_path):
    c=Catalog(tmp_path/'catalog.db');p=listing();c.ingest(bundle([p]))
    low=p.model_copy(update=dict(observed_at='2027-01-01T00:00:00+00:00',evidence_kind='editor_snapshot',readback_verified=False,fill_complete=False))
    assert c.ingest(bundle([low]))['older_skipped']==1
    assert c.find()['items'][0]['evidence_kind']=='persisted_readback'


def test_same_timestamp_conflict_is_not_arbitrarily_overwritten(tmp_path):
    c=Catalog(tmp_path/'catalog.db');p=listing();c.ingest(bundle([p]))
    changed=p.model_copy(deep=True);changed.variants[0].price='999'
    with pytest.raises(ValueError,match='同一采集时间'):c.ingest(bundle([changed]))


@pytest.mark.parametrize('url',['javascript:alert(1)','file:///etc/passwd','https://user:password@example.com'])
def test_unsafe_urls_rejected(url):
    data=listing().model_dump();data['source_url']=url
    with pytest.raises(ValidationError):Listing.model_validate(data)


def test_erp_icons_excluded_from_product_images():
    values=['https://pg-erp-cdn-ali.datacaciques.com/img/logo.png','data:image/png;base64,abc',
        'https://pg-cdn-a2.datacaciques.com/00/real-product.jpg','https://ps-cdn-s3.datacaciques.com/small/00/item.jpg']
    assert product_images(values)==values[2:]


def test_api_import_filters_export_and_origin(tmp_path,monkeypatch):
    client=TestClient(create_app(tmp_path/'api.db'));body=bundle([listing()]).model_dump()
    assert client.post('/api/imports',json=body,headers={'origin':'https://evil.example'}).status_code==403
    assert client.post('/api/imports',json=body).status_code==200
    monkeypatch.setenv('CATALOG_IMPORT_TOKEN','secret-test-token')
    assert client.post('/api/imports',json=body).status_code==401
    assert client.post('/api/imports',json=body,headers={'x-import-token':'secret-test-token'}).json()['unchanged']==1
    result=client.get('/api/products?q=B012345679&store=MYZ-L-21&status=complete').json()
    assert result['total']==1
    id=result['items'][0]['id'];detail=client.get('/api/products/'+id).json()
    assert detail['variants'][0]['copy']['description']=='A only'
    assert client.get('/api/products/missing').status_code==404
    assert client.get('/api/products?page=0').status_code==422
    assert client.get('/api/products?q=%27%20OR%201%3D1').json()['total']==0
    assert client.get('/api/export.csv?store=not-a-store').text.count('\n')==1
    exported=client.get('/api/export.json?namespace=test').json()
    assert ImportBundle.model_validate(exported).listings[0].variants[0].stock=='0'
    snapshot=detail['history'][0]['id']
    assert client.get(f'/api/products/{id}/snapshots/{snapshot}').json()['asin']=='B012345678'
    assert client.get('/').headers['content-security-policy'].find("script-src 'self'")>=0


def test_incomplete_and_empty_fields_preserved(tmp_path):
    data=listing().model_dump();data.update(fill_complete=False,readback_verified=False)
    data['fields']={'Net Content::measure':'','Brand Name':'Wrong Brand'}
    data['variants'][0].update(stock='0',ean='',price='0.00')
    p=Listing.model_validate(data);c=Catalog(tmp_path/'x.db');c.ingest(bundle([p]))
    got=c.get(c.find()['items'][0]['id'])
    assert got['fields']==data['fields'] and got['variants'][0]['stock']=='0'
    assert c.stats()['review']==1


def test_adapter_explicit_rename_links_without_row_order_inference(tmp_path):
    path=tmp_path/'audit.json';path.write_text('{}')
    params=dict(brand='Growlume',walmart_account='MYZ-L-21',price_multiplier='1.15')
    item=dict(sku='B012345678-p',template='exact',record_id='1')
    row={'1':{'label':'平台SKU','value':{'sku alias':'Growlume-12345679'}},
         '4':{'label':'价格','value':{'price':'30.99'}},'5':{'label':'在线库存','value':{'quantity':0}}}
    audit=dict(source=dict(sku=item['sku'],asin='B012345678',url='https://www.amazon.com/dp/B012345678'),
        runtime_parameters=params,erp_fill_audit=dict(audit=dict(listing_defaults=dict(cells=[
            dict(row='9',field='sku alias',expected='Growlume-12345679',status='verified'),
            dict(row='9',field='quantity',expected='0',status='verified',stock_decision={'asin':'B012345679','quantity':'0'})]))))
    rb=dict(status='verified',state=dict(fields={'templetName':'exact','dc_platformAccount':'MYZ-L-21'},rows={'Growlume-12345679':row}))
    p=normalize(audit,item,params,rb,'persisted_readback',path,'2026-10-23T00:00:00+00:00',True)
    assert p.variants[0].asin=='B012345679' and p.variants[0].stock=='0'
    audit['erp_fill_audit']={}
    p=normalize(audit,item,params,rb,'persisted_readback',path,'2026-10-23T00:00:00+00:00',True)
    assert p.variants[0].asin=='' # never infer B0 + branded SKU suffix


def test_source_audit_attribution_rejected(tmp_path):
    root=tmp_path/'logs';base=root/'batch';(base/'audits').mkdir(parents=True)
    manifest=dict(brand='Growlume',walmart_account='MYZ-L-21',price_multiplier='1.15',products=[dict(sku='B012345678',template='template',record_id='1')])
    (base/'manifest.json').write_text(json.dumps(manifest))
    (base/'audits/B012345678.json').write_text(json.dumps(dict(source=dict(sku='WRONG'),runtime_parameters={})))
    b,info=build_bundle(root)
    assert not b.listings and len(info['warnings'])==1


@pytest.mark.parametrize('asin', ['B099999999', ''])
def test_existing_child_asin_cannot_be_reassigned_or_erased(tmp_path,asin):
    c=Catalog(tmp_path/'catalog.db');p=listing();c.ingest(bundle([p]))
    changed=p.model_copy(deep=True);changed.observed_at='2026-10-24T00:00:00+00:00'
    changed.variants[0].asin=asin
    with pytest.raises(ValueError,match='子 SKU 身份冲突'):c.ingest(bundle([changed]))
    assert c.get(c.find()['items'][0]['id'])['variants'][0]['asin']=='B012345679'


def test_same_instant_different_timezone_cannot_overwrite(tmp_path):
    c=Catalog(tmp_path/'catalog.db');p=listing();c.ingest(bundle([p]))
    changed=p.model_copy(deep=True);changed.observed_at='2026-10-23T20:00:00+08:00'
    changed.variants[0].price='900'
    with pytest.raises(ValueError,match='同一采集时间'):c.ingest(bundle([changed]))
