import httpx
import pytest
import io
import zipfile

from catalog.images import HumanVerificationRequired, fetch_amazon_images
from test_editing import local, detail


def test_helper_download_contains_current_extension_source(local):
    client, _ = local
    response = client.get('/api/products/images/helper.zip')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {'manifest.json', 'popup.html', 'popup.js', 'bridge.js', 'worker.js', 'amazon.js'}


def test_challenge_returns_explicit_handoff_without_changing_product(local, monkeypatch):
    client, _ = local
    before = detail(client)
    def challenge(asin):
        raise HumanVerificationRequired('verify')
    monkeypatch.setattr('catalog.images.fetch_amazon_images', challenge)
    for sku, asin in [(None, before['asin']), ('child-0', before['variants'][0]['asin'])]:
        result = client.post(f"/api/products/{before['id']}/images/capture", json={'variant_sku': sku})
        assert result.status_code == 200
        assert result.json() == {'status': 'verification_required', 'asin': asin,
                                  'source_url': f'https://www.amazon.com/dp/{asin}?th=1&psc=1'}
    assert detail(client) == before


@pytest.mark.parametrize('status', [200, 503])
def test_http_challenge_detected_before_http_failure(status, monkeypatch):
    original = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text='Robot Check validateCaptcha'))
    monkeypatch.setattr('catalog.images.httpx.Client', lambda **kw: original(transport=transport, **kw))
    with pytest.raises(HumanVerificationRequired):
        fetch_amazon_images('B012345678')


def test_verified_browser_gallery_bound_to_child_and_not_saved(local):
    client, _ = local
    before = detail(client)
    asin = before['variants'][0]['asin']
    url = 'https://m.media-amazon.com/images/I/verified.jpg'
    body = {'variant_sku': 'child-0', 'source_url': f'https://www.amazon.com/dp/{asin}',
            'html': f'<input id="ASIN" value="{asin}"><img id="landingImage" data-old-hires="{url}">'}
    path = f"/api/products/{before['id']}/images/capture/browser"
    assert client.post(path, json=body).json()['images'] == [url]
    for change in [
        {'variant_sku': 'missing'}, {'source_url': 'https://evil.test/dp/' + asin},
        {'source_url': f'https://www.amazon.com:bad/dp/{asin}'},
        {'source_url': 'https://['},
        {'source_url': 'https://www.amazon.com/dp/B999999999'},
        {'html': body['html'].replace(asin, 'B999999999')},
        {'html': 'validateCaptcha' + body['html']},
        {'html': body['html'].replace('m.media-amazon.com', 'evil.test')},
    ]:
        assert client.post(path, json={**body, **change}).status_code in (409, 422)
    assert detail(client) == before
