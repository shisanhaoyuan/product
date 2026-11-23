import pytest

from catalog.models import ImportBundle
from test_editing import local, detail
from test_prelisting import product
from test_images import picture


def test_display_caches_owned_image_and_reuses_without_cdn(local, monkeypatch):
    client, catalog = local
    url = 'https://pg-cdn-a2.datacaciques.com/photo.jpg'
    catalog.ingest(ImportBundle(namespace='test', label='cover', listings=[product(9, images=[url])]))
    item = client.get('/api/products', params={'identifiers': 'parent-9'}).json()['items'][0]
    calls = []
    monkeypatch.setattr('catalog.listing_vision.fetch_image', lambda u: calls.append(u) or picture())
    path = f'/api/products/{item["id"]}/images/cached'
    response = client.get(path, params={'url': url})
    assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'
    assert response.headers['cache-control'] == 'private, max-age=86400'
    assert client.get(path, params={'url': url}, headers={
        'If-None-Match': response.headers['etag']}).status_code == 304
    assert client.get(path, params={'url': url}).content == response.content
    assert calls == [url]
    cover = client.get('/api/products', params={'identifiers': 'parent-9'}).json()['items'][0]
    assert cover['image'].startswith('/api/products/images/assets/')
    # Display cache must not mutate the imported gallery or the user's edits.
    assert client.get('/api/products/' + item['id']).json()['images'] == [url]
    other = detail(client)
    if other['id'] != item['id']:
        assert client.get(f'/api/products/{other["id"]}/images/cached', params={'url': url}).status_code == 404
    assert client.get(path, params={'url': 'https://pg-cdn-a2.datacaciques.com/unknown.jpg'}).status_code == 404
    for invalid in ['http://127.0.0.1/private', 'https://evil.example/photo.jpg',
                    'https://secret@pg-cdn-a2.datacaciques.com/photo.jpg']:
        assert client.get(path, params={'url': invalid}).status_code == 422
    assert calls == [url]


def test_cache_failure_returns_reason_and_does_not_poison_retry(local, monkeypatch):
    from catalog.listing_ai_client import ListingAIError
    client, catalog = local
    url = 'https://pg-cdn-a2.datacaciques.com/photo.jpg'
    catalog.ingest(ImportBundle(namespace='test', label='cover', listings=[product(9, images=[url])]))
    item = client.get('/api/products', params={'identifiers': 'parent-9'}).json()['items'][0]
    path = f'/api/products/{item["id"]}/images/cached'
    def fail(_):
        raise ListingAIError('图片服务器返回 HTTP 424')
    monkeypatch.setattr('catalog.listing_vision.fetch_image', fail)
    response = client.get(path, params={'url': url})
    assert response.status_code == 502 and '424' in response.json()['detail']
    assert response.headers['cache-control'] == 'no-store'
    monkeypatch.setattr('catalog.listing_vision.fetch_image', lambda _: picture())
    assert client.get(path, params={'url': url}).status_code == 200


@pytest.mark.parametrize('source,url', [
    ('Amazon', 'https://m.media-amazon.com/images/I/current.jpg'),
    ('GigaB2B', 'https://images.gigab2b.com/current.jpg'),
    ('GigaB2B_CA', 'https://images.gigab2b.com/current.jpg'),
    ('Saleyee', 'https://image.saleyee.com/current.jpg'),
])
def test_supplier_images_cached_once_and_bound_to_product(local, monkeypatch, source, url):
    client, catalog = local
    entry = product(9, images=[url])
    entry.source = source
    catalog.ingest(ImportBundle(namespace='test', label='supplier', listings=[entry]))
    item = client.get('/api/products', params={'source': source, 'identifiers': 'parent-9'}).json()['items'][0]
    calls = []
    monkeypatch.setattr('catalog.listing_vision.fetch_image', lambda u: calls.append(u) or picture())
    path = f'/api/products/{item["id"]}/images/cached'
    response = client.get(path, params={'url': url})
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'private, max-age=86400'
    assert client.get(path, params={'url': url}).content == response.content
    assert calls == [url]
    unknown = client.get(path, params={'url': url.replace('current.jpg', 'unknown.jpg')})
    assert unknown.status_code == 404 and unknown.headers['cache-control'] == 'no-store'
