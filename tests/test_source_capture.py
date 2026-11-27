import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.models import ImportBundle
from catalog.source_capture import amazon_source
from catalog.images import HumanVerificationRequired
from test_editing import local, detail
from test_auth import team, login, ORIGIN
from test_prelisting import product


@pytest.mark.parametrize('marketplace', ['US', 'CA'])
@pytest.mark.parametrize('unit_price', ['80.99', '40.50'])
def test_unit_price_does_not_conflict_with_struck_original(marketplace, unit_price):
    html = Path(__file__).with_name('fixtures').joinpath('amazon_ca_unit_price.html').read_text()
    html = html.replace('class="a-offscreen">$80.99', f'class="a-offscreen">${unit_price}')
    if marketplace == 'US':
        html = html.replace('M5V 3L9', '10001')
    result = amazon_source(html, 'B0FRG4YN3M', marketplace)
    assert result['status'] == 'captured'
    assert result['price'] == '80.99'
    assert result['original_price'] == result['basis_price'] == '95.99'
    assert result['original_price_state'] == 'verified'


@pytest.mark.parametrize('unit_markup', [
    '<span class="pricePerUnit"><span class="a-price"><span class="a-offscreen">$40.50</span></span> / count</span>',
    '<span class="contains-ppu"><span class="a-price"><span class="a-price-symbol">$</span><span class="a-price-whole">40</span><span class="a-price-fraction">50</span></span></span>',
])
def test_unit_price_is_neither_current_nor_reference(unit_markup):
    html = page(content='<div id="corePrice_feature_div"><span class="a-price"><span class="a-offscreen">$80.99</span></span>' + unit_markup + '</div>')
    result = amazon_source(html, 'B000000000')
    assert result['price'] == '80.99'
    assert result['original_price'] is None
    assert result['original_price_state'] == 'not_displayed'


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
    base = page(content='') + '<a id="aod-ingress-link">Other sellers on Amazon</a>'
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
    assert not amazon_source(base + '<div id="aod-offer-list">' + excluded + '</div>', 'B000000000').get('price')


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
    assert amazon_source(page(content='') + panel, 'B000000000')['price'] == '18.00'


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


def test_background_child_capture_persists_partial_quotes_separately(local,monkeypatch):
    client,catalog=local
    p=detail(client);children=p['variants']
    calls=[]
    def fetch(asin):
        calls.append(asin)
        return page(asin, offer(price='10.00' if asin==children[0]['asin'] else '20.00',original='30.00').replace('<a id="sellerProfileTriggerId">Garden Store</a>',''))
    monkeypatch.setattr('catalog.source_capture.fetch_amazon_page',fetch)
    path=f"/api/products/{p['id']}/source-capture"
    for child in children:
        result=client.post(path,json={'variant_sku':child['sku'],'background':True})
        assert result.status_code==200 and result.json()['status']=='partial'
        assert result.json()['asin']==child['asin']
    assert calls==[v['asin'] for v in children]
    assert client.get(path,params={'variant_sku':children[0]['sku']}).json()['price']=='10.00'
    assert client.get(path,params={'variant_sku':children[1]['sku']}).json()['price']=='20.00'
    assert client.get(path).json() is None


def test_wraith_failed_experiment_is_readable_without_overwriting_product(local, monkeypatch):
    client, catalog = local
    before = detail(client)
    path = f"/api/products/{before['id']}/source-capture"
    def blocked(*args):
        error = HumanVerificationRequired('Wraith 测试：3/3 轮；仍未通过验证')
        error.wraith_test = dict(attempts=3, message=str(error), challenge_seen=True, challenge_cleared=False)
        raise error
    from catalog.images import HumanVerificationRequired
    monkeypatch.setattr('catalog.source_capture.collect_quote_page', blocked)
    monkeypatch.setattr('catalog.amazon_browser.configured', lambda:True)
    child = before['variants'][0]['sku']
    response = client.post(path, json={'variant_sku':child}).json()
    assert response['status']=='verification_required' and response['wraith_test']['attempts']==3
    assert client.get(path, params={'variant_sku':child}).json()['wraith_test']==response['wraith_test']
    assert client.get(path).json() is None
    assert detail(client)==before


def test_conflicting_current_prices_keep_original_currency_and_explain_candidates():
    content = offer(price='85.49', original='89.99') + '<div id="corePrice_feature_div"><span class="a-offscreen">$76.49</span></div>'
    result = amazon_source(page(content=content), 'B000000000')
    assert result['price'] is None
    assert result['original_price'] == result['basis_price'] == '89.99'
    assert result['currency'] == 'USD'
    assert any('85.49' in issue and '76.49' in issue for issue in result['issues'])


@pytest.mark.parametrize('references,state', [('', 'not_displayed'),
    ('<span class="a-text-price"><span class="a-offscreen">$149.99</span></span>', 'verified'),
    ('<span class="a-text-price"><span class="a-offscreen">$149.99 $159.99</span></span>', 'ambiguous')])
def test_absent_reference_is_distinct_from_conflicting_reference(references,state):
    html=page(content='<div id="corePrice_feature_div"><span class="a-price"><span class="a-offscreen">$143.99</span></span>'+references+'</div>')
    result=amazon_source(html,'B000000000')
    assert result['original_price_state']==state
    assert result['price']=='143.99'


def test_verified_ordinary_offer_does_not_require_browser_retry(monkeypatch):
    from catalog.source_capture import collect_quote_page
    monkeypatch.setenv('CATALOG_AMAZON_BROWSER_PYTHON','configured')
    def unexpected(asin): raise AssertionError('Verified quote should not need another browser fetch')
    monkeypatch.setattr('catalog.amazon_browser.fetch_page',unexpected)
    content=offer(price='143.99').replace('<span class="a-price a-text-price"><span class="a-offscreen">$43.99</span></span>','')
    _,quote,method=collect_quote_page('B000000000',lambda asin:page(asin,content))
    assert method=='server' and quote['price']=='143.99'
    assert quote['original_price'] is None


def test_legacy_prices_inactive_accordion_and_unloaded_offers():
    body='<span id="price_inside_buybox">$35.00</span><div class="a-accordion-row"><span class="a-offscreen">$1.00</span></div><a id="sellerProfileTriggerId">Store</a><div id="deliveryBlockMessage">FREE delivery tomorrow</div>'
    assert amazon_source(page(content=body),'B000000000')['price']=='35.00'
    incomplete=page(content=body).replace('id="desktop_buybox"','id="desktop_buybox" data-offers-incomplete="true"')
    result=amazon_source(incomplete,'B000000000')
    assert result['price'] == '35.00'
    result=amazon_source(incomplete.replace('$35.00',''), 'B000000000')
    assert result['price'] is None and '完整' in result['issues'][0]
    with pytest.raises(ValueError,match='ASIN'):
        amazon_source(page(content=body+'<input name="items[0.base][asin]" value="B999999999">'),'B000000000')


@pytest.mark.parametrize('market', ['US', 'CA'])
@pytest.mark.parametrize('drawer', ['entrance', 'multiple', 'incomplete'])
def test_main_quote_survives_other_seller_drawer(market, drawer):
    html = page(content=offer(price='85.49', original='95.99'))
    if drawer == 'entrance':
        html += '<a id="aod-ingress-link">Other sellers on Amazon</a>'
    else:
        row = '<div id="aod-offer"><div id="aod-offer-heading">New</div>' + offer(price='22.00') + '</div>'
        html += '<div id="aod-offer-list">' + row * 2 + '</div>'
    if drawer == 'incomplete':
        html = html.replace('id="desktop_buybox"', 'id="desktop_buybox" data-offers-incomplete="true"')
    if market == 'CA':
        html = html.replace('10001', 'M5V 3L9')
    result = amazon_source(html, 'B000000000', market)
    assert result['price'] == '85.49' and result['original_price'] == '95.99'
    assert result['offer_scope'] == 'main_offer'
    assert result['status'] == 'captured'


def test_new_apex_accessibility_prices_and_seller_buttons_are_read():
    content='''<div id="apex_desktop"><span id="apex-pricetopay-accessibility-label" class="aok-offscreen">$85.49 with savings</span><span class="apex-basisprice-offscreen-label">Was: $95.99</span></div>
    <div tabular-attribute-name="Sold by"><button>Store A</button></div>
    <div id="deliveryMessageMirId">FREE delivery tomorrow</div>'''
    result=amazon_source(page(content=content),'B000000000')
    assert result['price']=='85.49' and result['original_price']=='95.99'
    assert result['seller']=='Store A' and result['shipping']=='0.00'


def test_partial_quote_keeps_wait_diagnostic():
    markup=page(content='<section id="catalog-main-offer" data-collector-offer-state="price_missing_after_wait" data-collector-offer-nodes="0"></section>')
    result=amazon_source(markup,'B000000000')
    assert result['offer_diagnostics']==[{'state':'price_missing_after_wait','nodes':'0'}]
    assert any('商品资料保留入库' in issue for issue in result['issues'])
