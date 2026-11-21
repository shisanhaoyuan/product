import json
import pytest

from catalog.source_images import collect_images, PREFIX, localize_row
from catalog.listing_ai_client import ListingAIError
from test_editing import local, detail, edit
from test_images import picture
from test_source_capture import page


def gallery(asin):
    return page(asin) + '<img id="landingImage" data-old-hires="https://m.media-amazon.com/images/I/current.jpg">'


def test_download_dedup_cache_and_sku_scoping(local, monkeypatch):
    c, catalog = local
    lid = detail(c)['id']; calls = []
    def fetch(url):
        calls.append(url)
        if url.endswith('broken.jpg'): raise ListingAIError('图片服务器拒绝访问（HTTP 403）')
        return picture()
    monkeypatch.setattr('catalog.listing_vision.fetch_image', fetch)
    urls = ['https://example.com/one.jpg', 'https://example.com/two.jpg', 'https://example.com/broken.jpg']
    first = collect_images(catalog, lid, 'child-0', urls + urls)
    assert first['state'] == 'partial' and len(first['images']) == 1
    assert len(first['saved']) == 2 and len(calls) == 3
    assert first['failures'] == [dict(index=3, reason='图片服务器拒绝访问（HTTP 403）')]
    assert c.get(first['images'][0]).status_code == 200
    collect_images(catalog, lid, 'child-0', urls[:2])
    assert len(calls) == 3
    collect_images(catalog, lid, 'second-child', urls[:1])
    assert len(calls) == 4  # A different SKU does not inherit the URL association.
    row = dict(visible=dict(mainImageUrl=urls[2], productSecondaryImageURL=urls[:2]))
    localize_row(row, first)
    assert row['visible']['mainImageUrl'] == first['images'][0]
    assert not row['visible']['productSecondaryImageURL']


def test_missing_cached_file_is_recollected(local, monkeypatch):
    c,catalog=local;lid=detail(c)['id'];calls=[]
    def fetch(url): calls.append(url);return picture()
    monkeypatch.setattr('catalog.listing_vision.fetch_image',fetch)
    url='https://example.com/photo.jpg'
    result=collect_images(catalog,lid,'child-0',[url])
    with catalog.connect() as db:db.execute('DELETE FROM image_assets')
    again=collect_images(catalog,lid,'child-0',[url])
    assert again['images']==result['images'] and len(calls)==2
    assert c.get(again['images'][0]).status_code==200


@pytest.mark.parametrize('method', ['background', 'browser'])
def test_quote_collection_saves_images_into_matching_product_only(local, monkeypatch, method):
    c,catalog=local;before=detail(c);child=before['variants'][0];calls=[]
    def fetch(url):calls.append(url);return picture()
    monkeypatch.setattr('catalog.listing_vision.fetch_image',fetch)
    monkeypatch.setattr('catalog.source_capture.fetch_amazon_page', lambda asin:gallery(asin))
    path=f"/api/products/{before['id']}/source-capture"
    payload=dict(variant_sku=child['sku'], background=True)
    if method=='browser':
        path+='/browser'
        payload=dict(variant_sku=child['sku'],source_url=f"https://www.amazon.com/dp/{child['asin']}",html=gallery(child['asin']))
    result=c.post(path,json=payload)
    assert result.status_code==200,result.text
    capture=result.json();assert capture['status']=='captured' and capture['price']=='37.99'
    images=capture['image_collection']['images'];assert images[0].startswith(PREFIX)
    after=detail(c)
    assert after['variants'][0]['images'][0]==images[0]
    assert after['variants'][1]['images']==before['variants'][1]['images']
    assert c.get(images[0]).status_code==200
    assert c.post(path,json=payload).status_code==200
    assert len(calls)==1
    stored=c.get(f"/api/products/{before['id']}/source-capture?variant_sku={child['sku']}").json()
    assert stored['image_collection']['images']==images


def test_manual_gallery_preserved_and_failed_images_do_not_lose_quote(local, monkeypatch):
    c,catalog=local;p=detail(c);child=p['variants'][0]
    monkeypatch.setattr('catalog.source_capture.fetch_amazon_page',lambda asin:gallery(asin))
    monkeypatch.setattr('catalog.listing_vision.fetch_image',lambda url:picture())
    assert edit(c,dict(field='images',variant_sku=child['sku']),['https://example.com/manual.jpg']).status_code==200
    path=f"/api/products/{p['id']}/source-capture"
    result=c.post(path,json=dict(variant_sku=child['sku'],background=True)).json()
    assert result['image_collection']['images']
    assert detail(c)['variants'][0]['images']==['https://example.com/manual.jpg']
    def fail(url):raise ListingAIError('图片域名未获准，请上传图片')
    monkeypatch.setattr('catalog.listing_vision.fetch_image',fail)
    with catalog.connect() as db:db.execute('DELETE FROM image_assets')
    result=c.post(path,json=dict(variant_sku=child['sku'],background=True)).json()
    assert result['price']=='37.99' and result['image_collection']['state']=='failed'
    assert '域名' in result['image_collection']['failures'][0]['reason']


def test_wrong_asin_never_downloads_or_attaches(local, monkeypatch):
    c,catalog=local;p=detail(c)
    def fail(url):raise AssertionError('wrong identity must not download')
    monkeypatch.setattr('catalog.listing_vision.fetch_image',fail)
    response=c.post(f"/api/products/{p['id']}/source-capture/browser",json=dict(
        variant_sku='child-0',source_url=f"https://www.amazon.com/dp/{p['variants'][0]['asin']}",html=gallery('B999999999')))
    assert response.status_code==422
    assert detail(c)==p


def test_untrusted_urls_never_open_connection(local, monkeypatch):
    c,catalog=local
    def fail(*args,**kwargs):raise AssertionError('must validate domain before networking')
    monkeypatch.setattr('httpx.Client.stream',fail)
    result=collect_images(catalog,detail(c)['id'],'child-0',['http://127.0.0.1/private','https://evil.example/photo.jpg'])
    assert result['state']=='failed' and all('域名' in f['reason'] for f in result['failures'])


def test_collection_uses_configured_filesystem_storage(local, monkeypatch, tmp_path):
    from catalog.object_storage import configured_storage
    c,catalog=local
    monkeypatch.setenv('CATALOG_IMAGE_STORAGE','filesystem')
    monkeypatch.setenv('CATALOG_IMAGE_ROOT',str(tmp_path/'images'))
    monkeypatch.setattr('catalog.listing_vision.fetch_image',lambda url:picture())
    result=collect_images(catalog,detail(c)['id'],'child-0',['https://example.com/photo.jpg'])
    assert result['state']=='complete'
    with catalog.connect() as db:
        obj=db.execute('SELECT * FROM image_objects').fetchone()
        assert obj['backend']=='filesystem'
        assert db.execute('SELECT COUNT(*) FROM image_assets').fetchone()[0]==0
    assert configured_storage(catalog).get(obj)
    again=collect_images(catalog,detail(c)['id'],'child-0',['https://example.com/photo.jpg'])
    assert again['saved'][0]['cached'] and again['images']==result['images']


@pytest.mark.parametrize('host', ['ps-cdn-s3.datacaciques.com', 'pg-cdn-a2.datacaciques.com'])
def test_imported_erp_cdn_downloads_and_saves_without_extra_configuration(local, monkeypatch, host):
    import socket
    import httpx
    from catalog.listing_vision import fetch_image
    c,catalog=local
    monkeypatch.delenv('CATALOG_LISTING_IMAGE_HOSTS',raising=False)
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('1.1.1.1',443))])
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.url.host==host
        return httpx.Response(200,content=picture())
    monkeypatch.setattr('catalog.listing_vision.fetch_image',lambda url:fetch_image(url,httpx.MockTransport(handler)))
    result=collect_images(catalog,detail(c)['id'],'child-0',[f'https://{host}/00/product.jpg'])
    assert result['state']=='complete' and len(calls)==1
    assert c.get(result['images'][0]).status_code==200


@pytest.mark.parametrize('url', [
    'https://ps-cdn-s3.datacaciques.com.evil.test/product.jpg',
    'https://other.ps-cdn-s3.datacaciques.com/product.jpg',
    'https://www.datacaciques.com/product.jpg',
    'http://ps-cdn-s3.datacaciques.com/product.jpg',
    'https://secret:password@pg-cdn-a2.datacaciques.com/product.jpg',
    'https://pg-cdn-a2.datacaciques.com:8443/product.jpg',
])
def test_cdn_exception_does_not_allow_other_destinations_or_expose_secrets(monkeypatch,url):
    from catalog.listing_vision import allowed_image_url
    monkeypatch.delenv('CATALOG_LISTING_IMAGE_HOSTS',raising=False)
    with pytest.raises(ListingAIError) as error:allowed_image_url(url)
    assert '域名' in str(error.value) and 'secret' not in str(error.value) and 'password' not in str(error.value)


def test_erp_cdn_private_dns_and_redirect_still_rejected(monkeypatch):
    import socket
    import httpx
    from catalog.listing_vision import allowed_image_url,fetch_image
    url='https://ps-cdn-s3.datacaciques.com/product.jpg'
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    with pytest.raises(ListingAIError,match='内网'):allowed_image_url(url)
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('1.1.1.1',443))])
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(302,headers={'location':'https://127.0.0.1/private'})
    with pytest.raises(ListingAIError):fetch_image(url,httpx.MockTransport(handler))
    assert len(calls)==1


def test_image_failure_diagnostics_include_host_and_http_status_not_query(monkeypatch):
    import socket
    import httpx
    from catalog.listing_vision import allowed_image_url,fetch_image
    with pytest.raises(ListingAIError,match='unknown.example') as error:
        allowed_image_url('https://unknown.example/a.jpg?token=private-token')
    assert 'private-token' not in str(error.value)
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('1.1.1.1',443))])
    with pytest.raises(ListingAIError,match='HTTP 403') as error:
        fetch_image('https://pg-cdn-a2.datacaciques.com/a.jpg?token=private-token',
                    httpx.MockTransport(lambda r:httpx.Response(403)))
    assert 'pg-cdn-a2.datacaciques.com' in str(error.value) and 'private-token' not in str(error.value)


def test_erp_image_failure_recovers_exact_asin_without_overwriting_prices(local, monkeypatch):
    from catalog.source_images import recover_imported_images
    c,catalog=local;p=detail(c);child=p['variants'][0];calls=[]
    def quote(asin,fetch):
        calls.append(asin)
        return gallery(asin), {'price':'999'}, 'server'
    monkeypatch.setattr('catalog.source_capture.collect_quote_page',quote)
    monkeypatch.setattr('catalog.listing_vision.fetch_image',lambda url:picture())
    initial=dict(state='failed',images=[],saved=[],failures=[dict(index=1,reason='HTTP 424')],message='图片下载失败')
    item=dict(source='Amazon',listing_id=p['id'])
    result=recover_imported_images(catalog,item,child,['https://ps-cdn-s3.datacaciques.com/dead.jpg'],initial)
    assert result['state']=='partial' and result['recovery']=='amazon_current_asin'
    assert calls==[child['asin']] and c.get(result['images'][0]).status_code==200
    assert detail(c)==p  # Recovery is scoped to the listing gallery; no price/product overlay mutations.
    assert recover_imported_images(catalog,item,child,['https://ps-cdn-s3.datacaciques.com/dead.jpg'],result)==result
    assert len(calls)==1


def test_erp_recovery_captcha_does_not_loop_or_borrow_other_sku(local, monkeypatch):
    from catalog.source_images import recover_imported_images
    from catalog.images import HumanVerificationRequired
    c,catalog=local;p=detail(c);child=p['variants'][0];calls=[]
    def quote(asin,fetch):calls.append(asin);raise HumanVerificationRequired('captcha')
    monkeypatch.setattr('catalog.source_capture.collect_quote_page',quote)
    initial=dict(state='failed',images=[],saved=[],failures=[dict(index=1,reason='HTTP 424')],message='HTTP 424')
    item=dict(source='Amazon',listing_id=p['id']);urls=['https://pg-cdn-a2.datacaciques.com/dead.jpg']
    result=recover_imported_images(catalog,item,child,urls,initial)
    assert len(calls)==1 and result['state']=='failed' and '人机验证' in result['message']
    recover_imported_images(catalog,item|{'source':'Saleyee'},child,urls,initial)
    recover_imported_images(catalog,item,child|{'asin':''},urls,initial)
    assert len(calls)==1 and detail(c)==p


def test_partial_erp_failure_recovers_without_losing_good_images(local, monkeypatch):
    from catalog.source_images import recover_imported_images
    c,catalog=local;p=detail(c);child=p['variants'][0]
    good=PREFIX+'a'*64;new=PREFIX+'b'*64
    urls=['https://pg-cdn-a2.datacaciques.com/good.jpg','https://pg-cdn-a2.datacaciques.com/dead.jpg']
    initial=dict(state='partial',images=[good],saved=[dict(source_url=urls[0],url=good)],
                 failures=[dict(index=2,reason='HTTP 424')],failed_urls=[urls[1]],message='partial')
    calls=[]
    def fetch(asin,fetcher):calls.append(asin);return gallery(asin),{},'server'
    monkeypatch.setattr('catalog.source_capture.collect_quote_page',fetch)
    monkeypatch.setattr('catalog.source_images.collect_amazon_images',lambda *args:dict(
        images=[new],saved=[dict(source_url='https://m.media-amazon.com/images/I/new.jpg',url=new)],
        failures=[],failed_urls=[],message='saved'))
    result=recover_imported_images(catalog,dict(source='Amazon',listing_id=p['id']),child,urls,initial)
    assert calls==[child['asin']] and result['images']==[good,new]
    row=dict(visible=dict(mainImageUrl=urls[0],productSecondaryImageURL=[urls[1]]))
    localize_row(row,result)
    assert row['visible']==dict(mainImageUrl=good,productSecondaryImageURL=[new])
    assert result['failures']==initial['failures']  # Failed old links remain auditable.


def test_failed_hero_and_secondary_removed_even_when_recovery_is_blocked():
    good=PREFIX+'a'*64
    urls=['https://example.com/dead-main.jpg','https://example.com/dead-secondary.jpg',good]
    collection=dict(images=[good],saved=[dict(source_url=good,url=good)],failed_urls=urls[:2])
    row=dict(visible=dict(mainImageUrl=urls[0],productSecondaryImageURL=urls[1:]))
    localize_row(row,collection)
    assert row['visible']==dict(mainImageUrl=good,productSecondaryImageURL=[])
    row=dict(visible=dict(mainImageUrl=urls[0],productSecondaryImageURL=urls[1:2]))
    localize_row(row,dict(images=[],saved=[],failed_urls=urls[:2]))
    assert row['visible']==dict(mainImageUrl='',productSecondaryImageURL=[])


def test_vision_reads_only_collected_images_not_failed_old_links(local,monkeypatch):
    from types import SimpleNamespace
    from catalog.listing_vision import image_evidence
    c,catalog=local;p=detail(c)
    monkeypatch.setattr('catalog.listing_vision.fetch_image',lambda url:picture())
    collection=collect_images(catalog,p['id'],'child-0',['https://example.com/good.jpg'])
    local_url=collection['images'][0]
    def fail(url):raise AssertionError('must not download failed URL again')
    monkeypatch.setattr('catalog.listing_vision.fetch_image',fail)
    monkeypatch.setattr('catalog.listing_vision.capability',lambda *args:None)
    class AI:
        settings=SimpleNamespace(vision=True,model='test')
        def query(self,purpose,payload):
            return dict(source_sku='child-0',observations=[])
    row=dict(listing_id=p['id'],source_sku='child-0',product_type='Chairs',visible={
        'mainImageUrl':local_url,'productSecondaryImageURL':['https://example.com/dead.jpg']})
    _,report=image_evidence(catalog,row,dict(_collected_images=[local_url]),AI())
    assert report['state']=='complete' and report['warnings']==[]
