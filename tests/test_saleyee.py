"""Contract fixtures from Saleyee V2 Swagger; no supplier or Walmart live calls."""
import copy
import json
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.db import Catalog
from catalog.models import ImportBundle, Listing
from catalog.saleyee import (SaleyeeSettings, SaleyeeClient, SaleyeeError, SyncInput,
                             sync_page, summarize, listing_from_api)
from catalog.saleyee_prelisting import prepare
from test_prelisting import inputs, create
from test_auth import team, login
from test_walmart_accounts import vault, Remote, bind
from test_walmart_publishing import PublisherRemote, ready, send
from catalog.walmart_publishing_client import WalmartPublishingClient


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv('CATALOG_SALEYEE_ACCESS_TOKEN', 'private-test-token')
    monkeypatch.setenv('CATALOG_SALEYEE_ACCOUNT', 'team-us')
    return SaleyeeSettings()


class FakeClient:
    def __init__(self, skus=('S1', 'S2')):
        self.calls=[]
        self.details=[dict(Sku=s,EnName='Wood chair '+s,BrandName='SupplierBrand',
            CategoryFirstNameEN='Furniture',CategorySecondNameEN='Chairs',
            Published=True,IsProductAuth=True,DistributionLimit=1,
            GoodsImageList=[dict(ImageUrl='https://img.saleyee.com/other.jpg',Sort=0),
                            dict(ImageUrl='https://img.saleyee.com/main.jpg',Sort=9,IsMainImage=True)],
            ProductAttributeList=[dict(AttributeNameEn='Material',AttributeValue='Wood')],
            SpecWeight=10,MapGroupList=[dict(MapPlatformGoodsCode='component',MapQuantity=2)],
            GoodsDescriptionList=[dict(GoodsDescriptionLabelList=[dict(LabelName='英文')],
              GoodsDescriptionParagraphList=[dict(ParagraphName='Features',GoodsDescription='Solid wood chair',SortNo=1),dict(ParagraphName='Description',GoodsDescription='Natural finish for a comfortable dining room.',SortNo=2)])]) for s in skus]
        self.prices=[dict(Sku=s,WarehousePriceList=[dict(Store='US',StockCode='US-W',
            LogisticsProductCode='L1',ReceiveCountryCode='US',CurrencyCode='USD',OriginalPrice='67',SellingPrice='60')]) for s in skus]
        self.stock=[dict(Sku=s,ProductInventorySiteList=[dict(SiteHosts='US',ProductInventoryList=[
            dict(StockCode='US-W',StockName='West warehouse',Qty=25,MakeStockQty=500,
                 StockItemList=[dict(PlatformStockName='California',Qty=25)])])]) for s in skus]

    def query(self,operation,body):
        self.calls.append((operation,body))
        if operation=='detail':
            rows=self.details
            if body.get('Skus'):
                rows=[r for r in rows if r['Sku'] in body['Skus']]
            return copy.deepcopy(dict(ProductInfoList=rows,PageIndex=body['PageIndex'],PageTotal=1))
        rows=self.prices if operation=='price' else self.stock
        return copy.deepcopy([r for r in reversed(rows) if r['Sku'] in body['SkuList']])


def test_bearer_contract_retry_and_secret_redaction(settings,monkeypatch):
    monkeypatch.setattr('catalog.saleyee.time.sleep',lambda _:None)
    calls=[]
    def handler(request):
        calls.append(request)
        assert str(request.url)=='https://api.saleyee.com/api/v2/Product/QueryProductPrice'
        assert request.headers['Authorization']=='Bearer private-test-token'
        assert json.loads(request.content)=={'SkuList':['S1']}
        if len(calls)==1:return httpx.Response(429,headers={'Retry-After':'1'})
        return httpx.Response(200,json={'status':'success','data':[]})
    c=SaleyeeClient(settings,httpx.MockTransport(handler))
    assert c.query('price',{'SkuList':['S1']})==[] and len(calls)==2
    for status,payload in [(401,{}),(200,{'status':'error','message':settings.token}),
                            (200,{'status':'processing'}),(302,{})]:
        c=SaleyeeClient(settings,httpx.MockTransport(lambda r:httpx.Response(status,json=payload)))
        with pytest.raises(SaleyeeError) as error:c.query('price',{'SkuList':['S1']})
        assert settings.token not in str(error.value)
    with pytest.raises(KeyError):c.query('create_order',{})


def test_import_isolation_deduplication_and_price_stock_join(tmp_path,settings,monkeypatch):
    c=Catalog(tmp_path/'catalog.db');f=FakeClient()
    opts=SyncInput(shipping='2.33')
    assert sync_page(c,settings,opts,client=f)['created']==2
    p=c.find(source='Saleyee')['items'][0];d=c.get(p['id'])
    assert d['images'][0].endswith('main.jpg')
    assert d['seller']=='' and d['asin']=='' and d['variants']==[]
    assert d['source_facts']['landed']=='69.33' and d['stock']=='25'
    assert d['source_facts']['saleyee']['detail']['MapGroupList']
    assert sync_page(c,settings,SyncInput(),client=f)['unchanged']==2
    monkeypatch.setenv('CATALOG_SALEYEE_ACCESS_TOKEN','rotated-token')
    assert sync_page(c,SaleyeeSettings(),SyncInput(),client=f)['unchanged']==2
    monkeypatch.setenv('CATALOG_SALEYEE_ACCOUNT','another-account')
    assert sync_page(c,SaleyeeSettings(),opts,client=f)['created']==2
    c.ingest(ImportBundle(namespace='another-source',label='test',listings=[
        Listing.model_validate(d | dict(external_id='S1',sku='S1',source='GigaB2B')),
        Listing.model_validate(d | dict(external_id='amazon',sku='S1',asin='B000000001',source='Amazon'))]))
    assert c.stats()['products']==6
    assert c.find(source='Saleyee')['total']==4
    assert all('private-test-token' not in json.dumps(x) for x in c.export())


@pytest.mark.parametrize('failure',['missing','duplicate','wrong','negative','fraction','identity'])
def test_bad_page_is_atomic(tmp_path,settings,failure):
    c=Catalog(tmp_path/'atomic.db');f=FakeClient()
    sync_page(c,settings,SyncInput(),client=f);before=c.export()
    f.prices[0]['WarehousePriceList'][0]['OriginalPrice']='999'
    if failure=='missing':f.stock.pop()
    if failure=='duplicate':f.stock.append(copy.deepcopy(f.stock[0]))
    if failure=='wrong':f.stock[0]['Sku']='WRONG'
    if failure=='negative':f.stock[0]['ProductInventorySiteList'][0]['ProductInventoryList'][0]['Qty']=-1
    if failure=='fraction':f.stock[0]['ProductInventorySiteList'][0]['ProductInventoryList'][0]['Qty']=1.5
    if failure=='identity':f.details[0]['Sku']='WRONG'
    with pytest.raises(SaleyeeError):sync_page(c,settings,SyncInput(skus=['S1','S2']),client=f)
    assert c.export()==before
    with c.connect() as db:assert db.execute('SELECT COUNT(*) FROM integration_leases').fetchone()[0]==0


def test_chunking_and_pagination_validation(tmp_path,settings):
    c=Catalog(tmp_path/'page.db');f=FakeClient(tuple('S'+str(i) for i in range(61)))
    assert sync_page(c,settings,SyncInput(),client=f)['created']==61
    assert max(len(b['SkuList']) for op,b in f.calls if op!='detail')==30
    assert len([op for op,b in f.calls if op=='price'])==3
    class Bad(FakeClient):
        def query(self,op,body):
            value=super().query(op,body)
            if op=='detail':value['PageIndex']=2
            return value
    with pytest.raises(SaleyeeError):sync_page(c,settings,SyncInput(),client=Bad())


@pytest.mark.parametrize('qty,expected',[(0,'0'),(9,'0'),(10,'10'),(11,'10'),(None,None)])
def test_prelisting_multiplier_and_stock(qty,expected,settings):
    f=FakeClient(('S1',));f.stock[0]['ProductInventorySiteList'][0]['ProductInventoryList'][0]['Qty']=qty
    item=listing_from_api('S1',f.details[0],f.prices[0],f.stock[0],SyncInput(shipping='2.33'),settings.namespace,'2026-11-19T00:00:00+00:00').model_dump()
    rows,errors,_=prepare(item,item,Decimal('1.4'))
    assert rows[0]['price']=='97.06' and rows[0]['stock']==expected
    assert bool(errors)==(qty is None or qty<10)
    assert rows[0]['content']['shipping_weight']==''


def test_ambiguous_quotes_unknown_shipping_and_warehouse_mismatch(settings):
    f=FakeClient(('S1',));d,p,s=f.details[0],f.prices[0],f.stock[0]
    assert summarize(d,p,s,SyncInput())['landed'] is None
    assert summarize(d,p,s,SyncInput(shipping='0'))['landed']=='67'
    p['WarehousePriceList'].append(p['WarehousePriceList'][0]|dict(LogisticsProductCode='L2'))
    assert summarize(d,p,s,SyncInput(shipping='0'))['price'] is None
    assert summarize(d,p,s,SyncInput(logistics='L1',shipping='0'))['landed']=='67'
    s['ProductInventorySiteList'][0]['SiteHosts']='UK'
    assert summarize(d,p,s,SyncInput(logistics='L1',shipping='0'))['inventory'] is None


def test_refresh_options_permissions_and_unknown_filter(tmp_path,settings,monkeypatch):
    app=create_app(tmp_path/'refresh.db');c=app.state.catalog;f=FakeClient(('S1',))
    sync_page(c,settings,SyncInput(),client=f);pid=c.find()['items'][0]['id'];client=TestClient(app)
    monkeypatch.setattr('catalog.saleyee.SaleyeeClient',lambda _:f)
    url=f'/api/integrations/saleyee/products/{pid}/refresh'
    r=client.post(url,json={'shipping':'2.33','warehouse':'US-W','logistics':'L1','quote_site':'US'})
    assert r.status_code==200,r.text
    assert r.json()['source_facts']['landed']=='69.33'
    sync_page(c,settings,SyncInput(),client=f)
    assert c.get(pid)['source_facts']['shipping']=='2.33'
    assert client.post(url,json={'skus':['OTHER']}).status_code==422
    assert client.post(url,json={'shipping':'-1'}).status_code==422
    assert c.find(source='Saleyee',inventory_status='out_of_stock')['total']==0
    f.stock[0]['ProductInventorySiteList'][0]['ProductInventoryList'][0]['Qty']=0
    sync_page(c,settings,SyncInput(),client=f)
    assert c.find(source='Saleyee',inventory_status='out_of_stock')['total']==1
    f.stock[0]['ProductInventorySiteList'][0]['ProductInventoryList'][0]['Qty']=None
    sync_page(c,settings,SyncInput(),client=f)
    assert c.find(source='Saleyee',inventory_status='out_of_stock')['total']==0
    monkeypatch.setenv('CATALOG_SALEYEE_ACCOUNT','other')
    assert client.post(url,json={}).status_code==409


def test_viewer_cannot_sync(team,settings):
    client,_=login(team,'viewer')
    assert client.post('/api/integrations/saleyee/sync',json={}).status_code==403
    assert client.post('/api/integrations/saleyee/products/fake/refresh',json={}).status_code==403
    assert settings.token not in client.get('/api/integrations/saleyee').text


def test_ai_uses_only_same_sku_allowlisted_facts(settings):
    from catalog.listing_generation import source_facts
    from catalog.listing_ai_client import ListingAIError
    f=FakeClient(('S1',))
    item=listing_from_api('S1',f.details[0],f.prices[0],f.stock[0],SyncInput(),settings.namespace,'2026-11-19T00:00:00+00:00').model_dump()
    rows,_,_=prepare(item,item,Decimal('1'))
    facts=source_facts(item,item,rows[0])
    assert facts['detail.ProductAttributeList'][0]['AttributeValue']=='Wood'
    assert 'verified_source' not in facts and 'detail.SpecWeight' not in facts
    item['source_facts']['saleyee']['detail']['Sku']='WRONG'
    with pytest.raises(ListingAIError):source_facts(item,item,rows[0])


def test_saleyee_through_walmart_submission_and_review(tmp_path,settings,vault):
    app=create_app(tmp_path/'publishing.db');c=TestClient(app);catalog=app.state.catalog;f=FakeClient(('S1',))
    sync_page(catalog,settings,SyncInput(shipping='2.33'),client=f)
    b,_=create(c,inputs(c,filters={'source':'Saleyee'},multiplier='1.4',brand='SupplierBrand'))
    Remote().install(app);bind(c,b['preview']['store']['id'])
    remote=PublisherRemote();app.state.walmart_publisher=WalmartPublishingClient(httpx.MockTransport(remote.handler))
    base=f"/api/prelisting/batches/{b['id']}/walmart"
    response=c.post(base+'/prepare');assert response.status_code==200,response.text
    doc=response.json()
    for row in doc['rows']:
        row['visible'].pop('productSecondaryImageURL',None)  # Test schema has no secondary-image field.
        row['identification']={'type':'EAN','value':'9780306406157','mode':'identifier','submission_id':''}
    r=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')});assert r.status_code==200,r.text
    env=(c,catalog,f,settings,b,base,remote)
    doc=ready(env)
    assert doc['validation']['valid_count']==1,json.dumps(doc['validation'],ensure_ascii=False)
    attempt=send(env,doc)
    assert attempt['feed_id'] and len(remote.posts)==1
    assert list(remote.feeds.values())[0]['MPItem'][0]['Orderable']['price']==97.06
    # Replaying the same SKU with another request key must not submit a second feed.
    from test_walmart_publishing import submission
    assert c.post(base+'/submit',json=submission(doc)).status_code==409
    r=c.post(base+f"/attempts/{attempt['id']}/refresh")
    assert r.status_code==200,r.text
    assert all(x['status']=='published' for x in r.json()['results'].values())
    assert list(remote.stock.values())==[10]


@pytest.mark.parametrize('changes',[
    {'Published':False}, {'IsProductAuth':False}, {'DistributionLimit':None},
    {'DistributionLimit':0,'GoodsDistributionPlatformDetailList':[{'DistributionPlatformDisplay':'Walmart'}]},
    {'TortInfo':{'TortStatus':0}}, {'TortInfo':{'TortStatus':1}},
])
def test_distribution_restrictions_block_prelisting(settings,changes):
    f=FakeClient(('S1',));f.details[0].update(changes)
    item=listing_from_api('S1',f.details[0],f.prices[0],f.stock[0],SyncInput(shipping='0'),settings.namespace,'2026-11-19T00:00:00+00:00').model_dump()
    assert prepare(item,item,Decimal('1.4'))[1]


def test_manual_shipping_never_follows_changed_quote(tmp_path,settings):
    c=Catalog(tmp_path/'quotes.db');f=FakeClient(('S1',))
    sync_page(c,settings,SyncInput(shipping='9.99'),client=f)
    f.prices[0]['WarehousePriceList'][0]['LogisticsProductCode']='CHANGED'
    sync_page(c,settings,SyncInput(),client=f)
    item=c.get(c.find()['items'][0]['id'])
    assert item['source_facts']['shipping'] is None
    assert item['source_facts']['landed'] is None


def test_shipping_absence_blocks_walmart_validation(tmp_path,settings,vault):
    app=create_app(tmp_path/'shipping.db');c=TestClient(app);f=FakeClient(('S1',))
    sync_page(app.state.catalog,settings,SyncInput(),client=f)
    b,_=create(c,inputs(c,filters={'source':'Saleyee'}))
    assert b['preview']['ready_count']==0
    Remote().install(app);bind(c,b['preview']['store']['id'])
    remote=PublisherRemote();app.state.walmart_publisher=WalmartPublishingClient(httpx.MockTransport(remote.handler))
    base=f"/api/prelisting/batches/{b['id']}/walmart"
    r=c.post(base+'/prepare');assert r.status_code==200,r.text
    assert c.post(base+'/taxonomy').status_code==200
    validation=c.post(base+'/validate',json={'revision':r.json()['revision']})
    assert validation.status_code==200,validation.text
    doc=validation.json()
    assert doc['validation']['valid_count']==0 and not remote.posts
    assert any('运费' in e['message'] for row in doc['validation']['rows'].values() for e in row['errors'])
