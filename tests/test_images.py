import io
import json
import sqlite3

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.images import amazon_images
from catalog.models import ImportBundle
from test_editing import local, detail, edit
from test_auth import team, login, ORIGIN
from test_prelisting import product


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
    assert TestClient(team, base_url=ORIGIN).get(url).status_code == 401
    admin.headers.pop('X-CSRF-Token')
    assert admin.post(path, content=picture()).status_code == 403


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
    assert client.post(path + '/capture', json={}).status_code == 200
    assert calls == [before['asin']]
    assert detail(client) == saved
    assert client.post(path + '/capture', json={'asin': 'B999999999'}).status_code == 422
    assert client.post(path + '/upload?variant_sku=wrong', content=picture()).status_code == 409
    assert edit(client, {'field': 'images'}, []).status_code == 200
    assert detail(client)['images'] == []
    assert detail(client)['variants'] == before['variants']
