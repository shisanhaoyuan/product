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
