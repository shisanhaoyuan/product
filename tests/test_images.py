import io
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.images import amazon_images
from catalog.models import ImportBundle
from test_editing import local, detail, edit
from test_auth import team, login, ORIGIN
from test_prelisting import product


def test_experiment_gallery_includes_hidden_images_and_main_without_thumbnail_duplicates():
    body = (Path(__file__).parent / 'fixtures/amazon_experiment_gallery.html').read_text()
    result = amazon_images(body, 'B0H9K5TQF9')
    ids = ['71d5dMpCQNL', '71ZAHHFmWJL', '61ZltEV70DL', '61ZBGpBtb3L',
           '61FQMw+nU1L', '71IZkvtwVwL', '71LgucZSOhL', '81r7Et88JsL']
    assert result == [f'https://m.media-amazon.com/images/I/{ident}._AC_SL{1500 if n in (0, 7) else 1250}_.jpg'
                      for n, ident in enumerate(ids)]


@pytest.mark.parametrize('url', [
    'https://evil.test/images/W/test/images/I/photo.jpg',
    'https://m.media-amazon.com.evil.test/images/I/photo.jpg',
    'https://user:pass@m.media-amazon.com/images/I/photo.jpg',
    'https://m.media-amazon.com:444/images/I/photo.jpg',
    'https://m.media-amazon.com/images/W/test/other/photo.jpg',
])
def test_experiment_path_does_not_relax_image_host_and_scope(url):
    from catalog.images import amazon_image_url
    assert amazon_image_url(url) == ''


def test_experiment_dom_only_main_is_normalized():
    body = '<input id="ASIN" value="B012345678"><img id="landingImage" data-old-hires="https://m.media-amazon.com/images/W/BW_TEST-T4/images/I/main._AC_SL1500_.jpg">'
    assert amazon_images(body, 'B012345678') == ['https://m.media-amazon.com/images/I/main._AC_SL1500_.jpg']


def test_browser_capture_reports_partial_download_without_overwriting_library(local, monkeypatch):
    from catalog.listing_ai_client import ListingAIError
    client, _ = local
    before = detail(client)
    asin = before['variants'][0]['asin']
    body = (Path(__file__).parent / 'fixtures/amazon_experiment_gallery.html').read_text().replace('B0H9K5TQF9', asin)
    calls = []

    def fetch(url):
        calls.append(url)
        if '71d5dMpCQNL' in url:
            raise ListingAIError('图片服务器返回 HTTP 403')
        out = io.BytesIO()
        Image.new('RGB', (48, 48), tuple(hashlib.sha256(url.encode()).digest()[:3])).save(out, format='PNG')
        return out.getvalue()

    monkeypatch.setattr('catalog.listing_vision.fetch_image', fetch)
    response = client.post(f"/api/products/{before['id']}/images/capture/browser", json={
        'variant_sku': 'child-0', 'source_url': f'https://www.amazon.com/dp/{asin}', 'html': body,
    })
    assert response.status_code == 200, response.text
    result = response.json()['image_collection']
    assert result['state'] == 'partial' and result['discovered_count'] == 8
    assert len(result['saved']) == 7 and len(calls) == 8
    assert result['failures'] == [{'index': 1, 'reason': '图片服务器返回 HTTP 403'}]
    assert all('/images/W/' not in url for url in calls)
    assert detail(client) == before


def picture():
    out = io.BytesIO()
    Image.new('RGB', (48, 48), '#517db6').save(out, format='PNG')
    return out.getvalue()


def test_upload_save_backup_isolated(local, tmp_path):
    client, catalog = local
    before = detail(client)
    path = f"/api/products/{before['id']}/images/upload?variant_sku=child-0"
    response = client.post(path, content=picture(), headers={'content-type': 'image/png'})
    assert response.status_code == 200, response.text
    url = response.json()['url']
    assert client.get(url).headers['content-type'] == 'image/jpeg'
    assert client.post(path, content=picture()).json()['url'] == url
    assert edit(client, {'field': 'images', 'variant_sku': 'child-0'}, [url]).status_code == 200
    after = detail(client)
    assert after['variants'][0]['images'] == [url]
    assert after['variants'][1] == before['variants'][1]
    backup = tmp_path / 'backup.db'
    with catalog.connect() as src, sqlite3.connect(backup) as dst:
        src.backup(dst)
    moved = TestClient(create_app(backup))
    assert moved.get(url).content == client.get(url).content
    assert detail(moved)['variants'][0]['images'] == [url]
    assert client.post(path, content=b'<svg onload="alert(1)"/>').status_code == 422
    assert client.post(path, content=b'x' * (8 * 1024 * 1024 + 1)).status_code == 413
    assert client.post(path.replace('child-0', 'wrong'), content=picture()).status_code == 409


def test_capture_parser_only_current_gallery():
    html = '''<input id="ASIN" value="B012345678"><script>
      'colorImages': { 'initial': [{"hiRes":"https://m.media-amazon.com/images/I/current.jpg"},
      {"hiRes":null,"large":"https://m.media-amazon.com/images/I/second.jpg"}],
      'other':[{"hiRes":"https://m.media-amazon.com/images/I/wrong.jpg"}]}
      </script><img src="https://m.media-amazon.com/images/I/recommendation.jpg">'''
    assert amazon_images(html, 'B012345678') == ['https://m.media-amazon.com/images/I/current.jpg',
                                                'https://m.media-amazon.com/images/I/second.jpg']
    for invalid in [html.replace('B012345678', 'B999999999'), 'validateCaptcha' + html,
                    html.replace('m.media-amazon.com', 'evil.test'), html.replace('id="ASIN"', 'id="unrelated"')]:
        with pytest.raises(ValueError): amazon_images(invalid, 'B012345678')


def test_capture_route_binds_stored_asin_and_does_not_save(local, monkeypatch):
    client, catalog = local
    p = detail(client)
    calls = []
    def capture(asin):
        calls.append(asin)
        return {'asin': asin, 'images': ['https://m.media-amazon.com/images/I/test.jpg']}
    monkeypatch.setattr('catalog.images.fetch_amazon_images', capture)
    monkeypatch.setattr('catalog.listing_vision.fetch_image', lambda url: picture())
    path = f"/api/products/{p['id']}/images/capture"
    assert client.post(path, json={'variant_sku': 'child-0'}).status_code == 200
    assert calls == [p['variants'][0]['asin']]
    assert detail(client) == p
    assert client.post(path, json={'variant_sku': 'child-0', 'asin': 'B999999999'}).status_code == 422
    assert client.post(path, json={'variant_sku': 'wrong'}).status_code == 409
    def fail(asin): raise ValueError('需要验证码')
    monkeypatch.setattr('catalog.images.fetch_amazon_images', fail)
    assert client.post(path, json={'variant_sku': 'child-0'}).status_code == 502
    assert detail(client) == p


def test_capture_wrapped_gallery_and_landing_fallback():
    gallery = [{'hiRes': 'https://m.media-amazon.com/images/I/one.jpg', 'label': "child's image"},
               {'large': 'https://m.media-amazon.com/images/I/two.jpg'}]
    quoted = repr(json.dumps(gallery))
    html = '<input id="ASIN" value="B012345678">' + "'colorImages': {'initial': A.$.parseJSON(" + quoted + ')}'
    assert amazon_images(html, 'B012345678') == [gallery[0]['hiRes'], gallery[1]['large']]
    fallback = '<input id="ASIN" value="B012345678"><img id="landingImage" data-old-hires="https://m.media-amazon.com/images/I/main.jpg">'
    assert amazon_images(fallback, 'B012345678') == ['https://m.media-amazon.com/images/I/main.jpg']


def test_image_access_permissions(team):
    team.state.catalog.ingest(ImportBundle(namespace='test', label='images', listings=[product()]))
    admin, _ = login(team)
    p = detail(admin)
    path = f"/api/products/{p['id']}/images/upload?variant_sku=child-0"
    url = admin.post(path, content=picture()).json()['url']
    viewer, _ = login(team, 'viewer')
    assert viewer.get(url).status_code == 200
    assert viewer.post(path, content=picture()).status_code == 403
    assert viewer.post(f"/api/products/{p['id']}/images/capture", json={'variant_sku':'child-0'}).status_code == 403
    browser_path = f"/api/products/{p['id']}/images/capture/browser"
    assert viewer.post(browser_path, json={}).status_code == 403
    assert TestClient(team, base_url=ORIGIN).post(browser_path, json={}).status_code == 401
    response = viewer.get(url)
    assert response.headers['cache-control'] == 'private, max-age=31536000, immutable'
    unauthorized = TestClient(team, base_url=ORIGIN).get(url, headers={'If-None-Match': response.headers['etag']})
    assert unauthorized.status_code == 401
    assert unauthorized.headers['cache-control'] == 'no-store'
    admin.headers.pop('X-CSRF-Token')
    assert admin.post(path, content=picture()).status_code == 403
    assert admin.post(browser_path, json={}).status_code == 403


def test_content_addressed_image_browser_cache_and_replacement(local):
    client, _ = local
    path = f"/api/products/{detail(client)['id']}/images/upload"
    url = client.post(path, content=picture()).json()['url']
    response = client.get(url)
    assert response.headers['cache-control'] == 'private, max-age=31536000, immutable'
    assert response.headers['vary'] == 'Cookie'
    etag = response.headers['etag']
    for condition in (etag, 'W/' + etag, '"other", ' + etag, '*'):
        cached = client.get(url, headers={'If-None-Match': condition})
        assert cached.status_code == 304 and cached.content == b''
        assert cached.headers['etag'] == etag
    assert client.get(url, headers={'If-None-Match': '"old"'}).content == response.content
    different = io.BytesIO()
    Image.new('RGB', (48, 48), 'red').save(different, format='PNG')
    updated_url = client.post(path, content=different.getvalue()).json()['url']
    assert updated_url != url
    assert client.get(updated_url, headers={'If-None-Match': etag}).status_code == 200
    assert client.get(updated_url).headers['etag'] != etag
    assert client.get(url).content == response.content
    for missing in ('invalid', 'a' * 64):
        result = client.get('/api/products/images/assets/' + missing, headers={'If-None-Match': '*'})
        assert result.status_code == 404 and result.headers['cache-control'] == 'no-store'
    assert client.get('/api/products').headers['cache-control'] == 'no-store'


def test_parent_image_import_capture_and_edit_isolation(local, monkeypatch):
    client, catalog = local
    before = detail(client)
    path = f"/api/products/{before['id']}/images"
    uploaded = client.post(path + '/upload', content=picture())
    assert uploaded.status_code == 200
    url = uploaded.json()['url']
    assert edit(client, {'field': 'images'}, [url]).status_code == 200
    saved = detail(client)
    assert saved['images'] == [url]
    assert saved['variants'] == before['variants']
    assert saved['asin'] == before['asin']
    calls = []
    def capture(asin):
        calls.append(asin)
        return {'asin': asin, 'images': ['https://m.media-amazon.com/images/I/parent.jpg']}
    monkeypatch.setattr('catalog.images.fetch_amazon_images', capture)
    monkeypatch.setattr('catalog.listing_vision.fetch_image', lambda url: picture())
    assert client.post(path + '/capture', json={}).status_code == 200
    assert calls == [before['asin']]
    assert detail(client) == saved
    assert client.post(path + '/capture', json={'asin': 'B999999999'}).status_code == 422
    assert client.post(path + '/upload?variant_sku=wrong', content=picture()).status_code == 409
    assert edit(client, {'field': 'images'}, []).status_code == 200
    assert detail(client)['images'] == []
    assert detail(client)['variants'] == before['variants']


def test_current_gallery_src_alternate_main_and_dynamic_resolution():
    asin='B012345678'
    first='https://m.media-amazon.com/images/I/main.jpg'
    high='https://m.media-amazon.com/images/I/large.jpg'
    low='https://m.media-amazon.com/images/I/small.jpg'
    identity=f'<input id="ASIN" value="{asin}">'
    assert amazon_images(identity+f'<img id="landingImage" src="{first}">',asin)==[first]
    assert amazon_images(identity+f'<img id="imgBlkFront" src="{first}">',asin)==[first]
    dynamic=json.dumps({low:[40,40],high:[1500,1500]})
    assert amazon_images(identity+f"<img id='landingImage' data-a-dynamic-image='{dynamic}'>",asin)==[high]
    body=identity+f'<section id="jiwu-image-gallery" data-asin="{asin}"><img src="{first}"><img src="{high}"></section><div id="recommend"><img src="{low}"></div>'
    assert amazon_images(body,asin)==[first,high]
    with pytest.raises(ValueError,match='图库 ASIN'):
        amazon_images(body.replace('data-asin="B012345678"','data-asin="B999999999"'),asin)
    invalid=identity+f'<div id="recommend"><img src="{first}"></div><div id="twister"><img src="{high}"></div>'
    with pytest.raises(ValueError,match='图片'):
        amazon_images(invalid,asin)


def test_empty_invalid_gallery_falls_back_to_visible_current_image():
    identity='<input id="ASIN" value="B012345678">'
    body=identity+'''<script>'colorImages':{'initial':[{'hiRes':'https://evil.test/wrong.jpg'}]}</script>'''
    image='https://m.media-amazon.com/images/I/main.jpg'
    assert amazon_images(body+f'<img id="landingImage" data-old-hires="https://evil.test/a.jpg" src="{image}">','B012345678')==[image]


def test_script_gallery_is_merged_with_full_browser_gallery():
    asin = 'B012345678'
    urls = [f'https://m.media-amazon.com/images/I/image-{index}.jpg' for index in range(8)]
    initial = json.dumps([{'hiRes': url} for url in urls[:3]])
    browser_gallery = ''.join(f'<img src="{url}">' for url in urls)
    html = (
        f'<input id="ASIN" value="{asin}"><script>"colorImages": {{"initial": {initial}}}</script>'
        f'<section id="jiwu-image-gallery" data-asin="{asin}">{browser_gallery}</section>'
    )

    assert amazon_images(html, asin) == urls


def test_script_gallery_only_is_not_taken_before_late_thumbnails():
    asin = 'B012345678'
    urls = [f'https://m.media-amazon.com/images/I/late-{index}.jpg' for index in range(8)]
    initial = json.dumps([{'hiRes': url} for url in urls[:3]])
    html = (
        f'<input id="ASIN" value="{asin}"><script>"colorImages": {{"initial": {initial}}}</script>'
        f'<section id="jiwu-image-gallery" data-asin="{asin}">'
        + ''.join(f'<img src="{url}">' for url in urls)
        + '</section>'
    )
    # Re-parsing after the live thumbnail gallery settles must recover every image,
    # even though Amazon's initial JSON contains only a subset.
    assert len(amazon_images(html, asin)) == 8


def test_static_missing_gallery_requests_browser_instead_of_stopping(local,monkeypatch):
    from catalog.images import ImageGalleryUnavailable
    client,_=local;p=detail(client)
    monkeypatch.setattr('catalog.images.fetch_amazon_page',lambda asin:f'<input id="ASIN" value="{asin}">')
    response=client.post(f"/api/products/{p['id']}/images/capture",json={'variant_sku':'child-0'})
    assert response.status_code==200,response.text
    assert response.json()['status']=='browser_required'
    assert response.json()['asin']==p['variants'][0]['asin']
    assert detail(client)==p
