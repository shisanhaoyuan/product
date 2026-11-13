import json

import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.models import ImportBundle
from catalog.source_capture import amazon_source
from catalog.images import HumanVerificationRequired
from test_editing import local, detail
from test_auth import team, login, ORIGIN
from test_prelisting import product


def offer(seller='Garden Store', delivery='FREE delivery Friday, November 6', price='37.99', original='43.99'):
    return f'''<div id="corePrice_feature_div"><span class="a-price"><span class="a-offscreen">${price}</span></span>
        <span class="a-price a-text-price"><span class="a-offscreen">${original}</span></span></div>
        <a id="sellerProfileTriggerId">{seller}</a><div id="deliveryBlockMessage">{delivery}</div>'''


def page(asin='B000000000', content=None):
    return f'''<input id="ASIN" value="{asin}"><span id="glow-ingress-line2">New York 10001</span>
        <div id="desktop_buybox">{offer() if content is None else content}</div>'''


def test_source_offer_original_price_and_shipping():
    result = amazon_source(page(), 'B000000000')
    assert result['status'] == 'captured'
    assert result['seller'] == 'Garden Store'
    assert result['price'] == '37.99'
    assert result['original_price'] == result['basis_price'] == '43.99'
    assert result['shipping'] == '0.00'
    assert result['delivery'] == 'FREE delivery Friday, November 6'


@pytest.mark.parametrize('delivery, expected', [
    ('FREE returns', None), ('FREE delivery on orders over $35', None),
    ('FREE delivery on $35 of items shipped by Amazon', None),
    ('FREE delivery with Prime', None), ('Delivery November 6', None),
    ('$5.99 delivery November 6', '5.99'), ('Shipping fee: $8.50', '8.50'),
    ('FREE delivery on your first order', None), ('CA$5.99 delivery', None),
    ('$12 delivery tomorrow', '12.00'), ('Shipping: $15', '15.00'),
])
def test_shipping_is_never_guessed(delivery, expected):
    result = amazon_source(page(content=offer(delivery=delivery)), 'B000000000')
    assert result['shipping'] == expected


def test_offer_panel_never_uses_pinned_or_arbitrary_offer():
    base = page() + '<a id="aod-ingress-link">Other sellers on Amazon</a>'
    assert amazon_source(base, 'B000000000')['status'] == 'browser_required'
    row = '<div id="aod-offer"><div id="aod-offer-heading">New</div>' + offer('Actual Seller', '$7.99 delivery November 9', '54.00', '60.00') + '</div>'
    pinned = '<div id="aod-pinned-offer">' + offer('Pinned', price='1.00') + '</div>'
    single = amazon_source(base + pinned + '<div id="aod-offer-list">' + row + '</div>', 'B000000000')
    assert single['seller'] == 'Actual Seller' and single['price'] == '54.00'
    assert single['shipping'] == '7.99'
    multiple = amazon_source(base + '<div id="aod-offer-list">' + row * 2 + '</div>', 'B000000000')
    assert multiple['status'] == 'partial' and not multiple.get('price')
    unreadable = row.replace('$54.00', 'unavailable').replace('$60.00', 'unavailable')
    multiple = amazon_source(base + '<div id="aod-offer-list">' + row + unreadable + '</div>', 'B000000000')
    assert multiple['status'] == 'partial' and not multiple.get('price')
    wrong = row.replace('New</div>', 'New</div><input name="ASIN" value="B999999999">')
    with pytest.raises(ValueError, match='ASIN'):
        amazon_source(base + '<div id="aod-offer-list">' + wrong + '</div>', 'B000000000')
    excluded = row.replace('New</div>', 'Used</div>')
    assert amazon_source(base + '<div id="aod-offer-list">' + excluded + '</div>', 'B000000000')['price'] == '37.99'


def test_seller_without_link_and_nested_delivery():
    content = offer().replace('<a id="sellerProfileTriggerId">Garden Store</a>',
        '<div tabular-attribute-name="Sold by"><span>Sold by</span><span>Amazon.com</span></div>')
    content = content.replace('FREE delivery Friday, November 6',
        '<span id="mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE">FREE delivery Friday, November 6</span>')
    result = amazon_source(page(content=content), 'B000000000')
    assert result['seller'] == 'Amazon.com'
    assert result['delivery'] == 'FREE delivery Friday, November 6'
    content = offer().replace('<a id="sellerProfileTriggerId">Garden Store</a>',
        '<div id="merchant-info">Ships from and sold by Amazon.com.</div>')
    assert amazon_source(page(content=content), 'B000000000')['seller'] == 'Amazon.com'


def test_price_requires_complete_parts_and_ignores_pinned_inside_list():
    parts = '<span class="a-price"><span aria-hidden="true"><span class="a-price-symbol">$</span><span class="a-price-whole">35<span>.</span></span><span class="a-price-fraction">99</span></span></span>'
    content = offer().replace('<span class="a-price"><span class="a-offscreen">$37.99</span></span>', parts)
    assert amazon_source(page(content=content), 'B000000000')['price'] == '35.99'
    missing = content.replace('<span class="a-price-fraction">99</span>', '')
    assert amazon_source(page(content=missing), 'B000000000')['price'] is None
    row = '<div id="aod-offer"><div id="aod-offer-heading">New</div>' + offer(price='18.00') + '</div>'
    panel = '<div id="aod-offer-list"><div id="aod-pinned-offer">' + row + '</div>' + row + '</div>'
    assert amazon_source(page() + panel, 'B000000000')['price'] == '18.00'


def test_identity_currency_and_recommendation_exclusion():
    with pytest.raises(ValueError, match='ASIN'):
        amazon_source(page('B999999999'), 'B000000000')
    with pytest.raises(HumanVerificationRequired):
        amazon_source('Robot Check', 'B000000000')
    result = amazon_source(page(content=offer().replace('$', 'CA$')), 'B000000000')
    assert result['price'] is None and result['shipping'] is None
    result = amazon_source(page(content=offer() + '<div id="recommendations">' + offer(price='3.00') + '</div>'), 'B000000000')
    assert result['price'] == '37.99'
    result = amazon_source(page().replace('10001', '90210'), 'B000000000')
    assert result['status'] == 'partial'


def test_save_reopen_and_parent_child_isolation(local, monkeypatch):
    client, catalog = local
    before = detail(client)
    path = f"/api/products/{before['id']}/source-capture"
    monkeypatch.setattr('catalog.source_capture.fetch_amazon_page', lambda asin: page(asin))
    result = client.post(path, json={})
    assert result.status_code == 200 and result.json()['price'] == '37.99'
    child = before['variants'][1]
    body = dict(variant_sku=child['sku'], source_url=f"https://www.amazon.com/dp/{child['asin']}",
                html=page(child['asin'], offer('Child Seller', '$4.99 delivery tomorrow', '72.00', '80.00')))
    assert client.post(path + '/browser', json=body).status_code == 200
    reopened = TestClient(create_app(catalog.path))
    assert reopened.get(path).json()['seller'] == 'Garden Store'
    assert reopened.get(path, params={'variant_sku': child['sku']}).json()['seller'] == 'Child Seller'
    assert reopened.get(path, params={'variant_sku': before['variants'][0]['sku']}).json() is None
    after = detail(client)
    assert after['seller'] == before['seller']  # Existing values are preserved.
    assert after['variants'][1]['seller'] == child['seller']
    assert after['variants'][1]['source_facts']['shipping'] == '4.99'
    for key in ('price', 'stock', 'msrp', 'copy', 'images', 'checks'):
        assert after[key] == before[key]
    for changes in [{'variant_sku': 'wrong'}, {'source_url': 'https://evil.test'},
                    {'html': page('B111111111')}, {'source_url': 'https://['},
                    {'html': 'Robot Check'}, {'asin': 'B111111111'}]:
        assert client.post(path + '/browser', json=body | changes).status_code in {409, 422}
    with catalog.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM source_captures').fetchone()[0] == 2


def test_challenge_does_not_write_and_identity_change_blocks_save(local, monkeypatch):
    client, catalog = local
    before = detail(client)
    path = f"/api/products/{before['id']}/source-capture"
    monkeypatch.setattr('catalog.source_capture.fetch_amazon_page', lambda asin: 'Robot Check')
    assert client.post(path, json={}).json()['status'] == 'verification_required'
    assert client.get(path).json() is None
    def change_identity(asin):
        with catalog.connect() as db:
            row = db.execute('SELECT data_json FROM listings WHERE id=?', (before['id'],)).fetchone()
            data = json.loads(row[0]); data['asin'] = 'B999999999'
            db.execute('UPDATE listings SET data_json=? WHERE id=?', (json.dumps(data), before['id']))
        return page(asin)
    monkeypatch.setattr('catalog.source_capture.fetch_amazon_page', change_identity)
    assert client.post(path, json={}).status_code == 409
    with catalog.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM source_captures').fetchone()[0] == 0


@pytest.mark.parametrize('role', ['admin', 'editor', 'viewer'])
def test_source_capture_permissions(team, role):
    team.state.catalog.ingest(ImportBundle(namespace='test', label='test', listings=[product()]))
    client, _ = login(team, role)
    p = detail(client)
    path = f"/api/products/{p['id']}/source-capture/browser"
    body = {'source_url': f"https://www.amazon.com/dp/{p['asin']}", 'html': page(p['asin'])}
    assert TestClient(team, base_url=ORIGIN).post(path, json=body).status_code == 401
    assert client.post(path, json=body).status_code == (403 if role == 'viewer' else 200)
    sync_path = f"/api/products/{p['id']}/source-capture/sync"
    assert client.post(sync_path, json={}).status_code == (403 if role == 'viewer' else 200)
    client.headers.pop('X-CSRF-Token')
    assert client.post(path, json=body).status_code == 403
    assert client.post(sync_path, json={}).status_code == 403
