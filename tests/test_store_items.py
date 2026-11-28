"""Bound-account manual actions: all supplier/Marketplace network is mocked."""
import json
import uuid
import httpx
import pytest
from fastapi.testclient import TestClient
from catalog.app import create_app
from catalog.walmart_publishing_client import WalmartPublishingClient
from test_walmart_accounts import vault, Remote, bind
from test_auth import team, login, ORIGIN
from test_saleyee import settings


class ItemsRemote(Remote):
    def __init__(self):
        super().__init__()
        self.price = 25
        self.quantity = 7
        self.life = 'ACTIVE'
        self.scopes = {'item':'full_access', 'inventory':'full_access', 'price':'full_access'}
        self.timeout = False
        self.delay = False
        self.wrong = False
        self.node = 'node-a'
        self.search_result = {'items': [{'itemId': '123', 'images': [{'url': 'https://i5.walmartimages.com/catalog.jpg'}]}]}
        self.search_status = 200

    @property
    def writes(self):
        return [r for r in self.calls if r.method in ('PUT','DELETE')]

    def item(self):
        return dict(sku='REMOTE-ONLY', productName='Remote cabinet', gtin='12345678901234',
                    publishedStatus='PUBLISHED', lifecycleStatus=self.life,
                    price=dict(currency='USD', amount=self.price))

    def handler(self, r):
        path = r.url.path
        if path in ('/v3/token','/v3/settings/partnerprofile'):
            return super().handler(r)
        self.calls.append(r)
        if path == '/v3/token/detail':
            return httpx.Response(200, json={'is_valid': True, 'scopes':self.scopes})
        if path == '/v3/settings/shipping/shipnodes':
            return httpx.Response(200,json=[dict(shipNode=self.node,shipNodeName='West', status='ACTIVE')])
        if path == '/v3/items' and r.method == 'GET':
            assert r.url.params.get('limit') == '50' or r.url.params.get('sku')
            return httpx.Response(200,json={'ItemResponse':[self.item()], 'nextCursor':'next-page', 'totalItems':2})
        if path == '/v3/items/REMOTE-ONLY':
            if r.method == 'GET':
                item = self.item()
                if self.wrong: item['sku'] = 'OTHER'
                return httpx.Response(200,json={'ItemResponse':[item]})
            assert r.method == 'DELETE'
            if not self.delay: self.life = 'RETIRED'
            return httpx.Response(200,json={'sku':'REMOTE-ONLY'})
        if path == '/v3/items/walmart/search':
            assert r.method == 'GET'
            assert r.url.params['gtin'] == '12345678901234'
            assert r.url.params['responseFormat'] == 'DEFAULT'
            return httpx.Response(self.search_status, json=self.search_result)
        if path == '/v3/price':
            assert r.method == 'PUT'
            assert json.loads(r.content)['sku'] == 'REMOTE-ONLY'
            if not self.delay: self.price = json.loads(r.content)['pricing'][0]['currentPrice']['amount']
            if self.timeout: raise httpx.ReadTimeout('PRIVATE',request=r)
            return httpx.Response(200,json={'sku':'REMOTE-ONLY'})
        if path == '/v3/inventory':
            assert r.url.params['shipNode'] == self.node and r.url.params['sku'] == 'REMOTE-ONLY'
            if r.method == 'PUT':
                if not self.delay: self.quantity = json.loads(r.content)['quantity']['amount']
            return httpx.Response(200,json={'sku':'REMOTE-ONLY','shipNode':self.node,'quantity':{'unit':'EACH','amount':self.quantity}})
        raise AssertionError((r.method, path))


@pytest.fixture
def offers(tmp_path, vault):
    app = create_app(tmp_path/'offers.db')
    client = TestClient(app)
    remote = ItemsRemote()
    remote.install(app)
    sid = client.get('/api/listing-stores').json()[0]['id']
    bound = bind(client, sid)
    app.state.walmart_publisher = WalmartPublishingClient(httpx.MockTransport(remote.handler))
    return client, remote, sid, bound


def change(bound, **kwargs):
    return dict(request_id=str(uuid.uuid4()), partner=bound['profile']['partnerId'],
                revision=bound['revision'], sku='REMOTE-ONLY', kind='price',
                expected='25', target='30', **kwargs)


def test_remote_listing_cursor_exact_sku_and_zero_local_products(offers):
    c, r, sid, _ = offers
    assert c.get('/api/products').json()['total'] == 0
    assert c.get('/api/store-items').json()[0]['id'] == sid
    response = c.get(f'/api/store-items/{sid}').json()
    assert response['items'][0]['sku'] == 'REMOTE-ONLY' and response['cursor'] == 'next-page'
    c.get(f'/api/store-items/{sid}?cursor=next-page')
    assert r.calls[-1].url.params['nextCursor'] == 'next-page'
    assert c.get(f'/api/store-items/{sid}?sku=NOT-EXIST').json()['items'] == []
    assert c.get('/api/store-items/99999').status_code == 404


@pytest.mark.parametrize('kind,target,expected,node', [
    ('price','19.99','25',''), ('stock','0','7','node-a'), ('stock','20','7','node-a'),
    ('retire','RETIRED','ACTIVE','')])
def test_manual_changes_allow_lower_price_restore_stock_and_retire(offers, kind, target, expected, node):
    c, r, sid, bound = offers
    data = change(bound) | dict(kind=kind,target=target,expected=expected,node=node,confirm_sku='REMOTE-ONLY' if kind=='retire' else '')
    url = f'/api/store-items/{sid}/changes'
    result = c.post(url,json=data)
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'verified'
    assert len(r.writes) == 1
    assert c.post(url,json=data).json()['status'] == 'verified' and len(r.writes) == 1
    assert c.post(url,json=data | {'target':'999'}).status_code in (409,422)
    assert c.get(f'/api/store-items/{sid}/history').json()[0]['target'] == target


@pytest.mark.parametrize('bad', [
    {'partner':'OTHER'}, {'revision':999}, {'expected':'20'}, {'sku':'NOT-EXIST'},
    {'target':'0'}, {'target':'NaN'}, {'target':'1.001'}, {'target':'-1'},
    {'kind':'stock','target':'-1','node':'node-a'}, {'kind':'stock','target':'1.5','node':'node-a'},
    {'kind':'stock','target':'2','node':'other'}, {'kind':'retire','target':'RETIRED','expected':'ACTIVE'},
])
def test_invalid_or_stale_input_never_writes(offers, bad):
    c, r, sid, bound = offers
    data = change(bound) | bad
    if bad.get('sku'): r.wrong = True; data['sku']='REMOTE-ONLY'
    res = c.post(f'/api/store-items/{sid}/changes',json=data)
    assert res.status_code in (409,422,502), res.text
    assert not r.writes


def test_missing_scope_no_write_or_secret_leak(offers):
    c, r, sid, bound = offers
    r.scopes['price'] = 'view_only'
    response = c.post(f'/api/store-items/{sid}/changes',json=change(bound))
    assert response.status_code == 502 and not r.writes
    assert 'PRIVATE' not in response.text


def test_timeout_blocks_replay_and_can_reconcile(offers):
    c, r, sid, bound = offers
    r.timeout = True
    data = change(bound)
    url = f'/api/store-items/{sid}'
    assert c.post(url+'/changes',json=data).json()['status'] == 'pending'
    assert c.post(url+'/changes',json=change(bound)).status_code == 409
    assert c.post(url+'/changes',json=data).json()['status'] == 'pending'
    assert len(r.writes) == 1
    result = c.post(url+f"/changes/{data['request_id']}/check")
    assert result.json()['status'] == 'verified'
    assert len(r.writes) == 1


def test_retirement_ack_not_mistaken_for_completed(offers):
    c, r, sid, bound = offers
    r.delay = True
    data = change(bound) | dict(kind='retire',expected='ACTIVE',target='RETIRED',confirm_sku='REMOTE-ONLY')
    url=f'/api/store-items/{sid}'
    assert c.post(url+'/changes',json=data).json()['status'] == 'pending'
    assert c.post(url+'/changes',json=change(bound)).status_code == 409
    assert c.post(url+f"/changes/{data['request_id']}/check").json()['status'] == 'pending'
    r.life = 'RETIRED'
    assert c.post(url+f"/changes/{data['request_id']}/check").json()['status'] == 'verified'


def test_viewer_can_read_but_not_write(team, vault):
    admin, _ = login(team,'admin')
    remote = ItemsRemote(); remote.install(team)
    sid = admin.get('/api/listing-stores').json()[0]['id']
    bound = bind(admin, sid)
    team.state.walmart_publisher = WalmartPublishingClient(httpx.MockTransport(remote.handler))
    outside = TestClient(team,base_url=ORIGIN)
    assert outside.get('/api/store-items').status_code == 401
    viewer, _ = login(team,'viewer')
    assert viewer.get(f'/api/store-items/{sid}').status_code == 200
    assert viewer.post(f'/api/store-items/{sid}/changes',json=change(bound)).status_code == 403
    editor, _ = login(team,'editor')
    assert editor.post(f'/api/store-items/{sid}/changes',json=change(bound)).status_code == 200


def test_automatic_patrol_guard_respects_pending_manual_change(offers):
    from catalog.giga_walmart_sync import check_identity
    from catalog.giga import sync_lease
    from catalog.walmart_client import WalmartError
    c, r, sid, bound = offers
    r.delay = True
    assert c.post(f'/api/store-items/{sid}/changes',json=change(bound)).json()['status']=='pending'
    catalog=c.app.state.catalog
    with sync_lease(catalog,'test') as a, sync_lease(catalog,'test.patrol') as b, catalog.connect() as db:
        with pytest.raises(WalmartError,match='人工修改待确认'):
            check_identity(db,dict(store_id=sid,partner_id=bound['profile']['partnerId'],platform_sku='REMOTE-ONLY'),
                           {},'test',a,b)


def test_api_image_and_unsafe_url_fallback(offers):
    c, remote, sid, _ = offers
    original = remote.item
    remote.item = lambda: original() | {'mainImageUrl':'https://i5.walmartimages.com/example.jpg'}
    row = c.get(f'/api/store-items/{sid}').json()['items'][0]
    assert row['image'].endswith('example.jpg') and row['image_source'] == 'walmart'
    remote.item = lambda: original() | {'mainImageUrl':'javascript:alert(1)'}
    row = c.get(f'/api/store-items/{sid}').json()['items'][0]
    assert row['image'] == '' and row['image_source'] == ''


def test_submitted_image_requires_same_store_partner_and_sku(tmp_path, settings, vault):
    from test_saleyee_patrol import live
    from catalog.store_items import attach_images
    # Exercise against a real submitted claim, not a draft or fuzzy source match.
    env, _, doc, _ = live.__wrapped__(tmp_path, settings, vault)
    sid = env[4]['preview']['store']['id']
    sku = doc['rows'][0]['platform_sku']
    def row(sku=sku):
        return dict(sku=sku,image='',image_source='')
    with env[1].connect() as db:
        for store, partner, item in ((sid,'other',row()), (sid+999,'100009',row()), (sid,'100009',row('OTHER'))):
            attach_images(db,[item],store,partner)
            assert item['image'] == ''
        item = row()
        attach_images(db,[item],sid,'100009')
        assert item['image'] == doc['rows'][0]['visible']['mainImageUrl']
        assert item['image_source'] == 'submitted'
        item.update(image='https://example.com/remote.jpg',image_source='walmart')
        attach_images(db,[item],sid,'100009')
        assert item['image'] == 'https://example.com/remote.jpg'


def image_request(c, sid, bound, **params):
    return c.get(f'/api/store-items/{sid}/image', params={
        'sku': 'REMOTE-ONLY', 'partner': bound['profile']['partnerId'], 'revision': bound['revision'], **params})


def test_external_offer_image_lookup_cached_without_modifying_offer(offers):
    c, remote, sid, bound = offers
    original = c.get(f'/api/store-items/{sid}').json()
    assert not original['items'][0]['image']
    result = image_request(c, sid, bound)
    assert result.status_code == 200, result.text
    assert result.json()['image'] == 'https://i5.walmartimages.com/catalog.jpg'
    assert result.json()['image_source'] == 'walmart'
    count = len(remote.calls)
    assert image_request(c, sid, bound).json() == result.json()
    assert len(remote.calls) == count
    assert image_request(c, sid, bound, revision=999).status_code == 409
    assert image_request(c, sid, bound, partner='OTHER').status_code == 409
    assert c.get(f'/api/store-items/{sid}').json() == original
    assert not remote.writes


@pytest.mark.parametrize('status,raw,expected', [
    (200, {}, 'missing'),
    (200, {'items': [{'gtin': '99999999999999', 'images': [{'url': 'https://example.com/wrong.jpg'}]}]}, 'missing'),
    (200, {'items': [{'images': [{'url': 'javascript:alert(1)'}]}]}, 'missing'),
    (200, {'items': [{}, {}]}, 'missing'),
    (429, {}, 'unavailable'),
    (403, {}, 'unavailable'),
])
def test_image_failures_do_not_break_listing(offers, status, raw, expected):
    c, remote, sid, bound = offers
    remote.search_status, remote.search_result = status, raw
    result = image_request(c, sid, bound)
    assert result.status_code == 200
    assert result.json()['image'] == '' and result.json()['image_status'] == expected
    assert c.get(f'/api/store-items/{sid}').status_code == 200
    assert not remote.writes


def test_image_requires_store_sku_and_server_gtin(offers):
    c, remote, sid, bound = offers
    remote.wrong = True
    assert image_request(c, sid, bound).json()['image_status'] == 'unavailable'
    assert not any(r.url.path.endswith('/walmart/search') for r in remote.calls)


@pytest.mark.parametrize('extra', [{'gtin': ''}, {'gtin': 'invalid'}, {'images': [{'url': 'https://example.com/direct.jpg'}]}])
def test_image_skips_search_when_not_needed_or_no_gtin(offers, extra):
    c, remote, sid, bound = offers
    original = remote.item
    remote.item = lambda: original() | extra
    result = image_request(c, sid, bound).json()
    assert result['image'] == ('https://example.com/direct.jpg' if 'images' in extra else '')
    assert not any(r.url.path.endswith('/walmart/search') for r in remote.calls)


def test_image_identity_and_local_asset_formats():
    from catalog.store_items import catalog_image, image_url
    image = 'https://example.com/correct.jpg'
    raw = {'items': [{'itemId': 'wrong', 'images': [{'url': 'https://example.com/wrong.jpg'}]},
                     {'itemId': 'correct', 'images': [{'url': image}]}]}
    assert catalog_image(raw, {'gtin': '00123456789012', 'item_id': 'correct'}) == image
    assert catalog_image(raw, {'gtin': '00123456789012', 'item_id': 'missing'}) == ''
    raw = {'items': [{'upc': '123456789012', 'images': [{'url': image}]}]}
    assert catalog_image(raw, {'gtin': '00123456789012'}) == image
    local = '/api/products/images/assets/' + 'a' * 64
    assert image_url(local) == local
    assert image_url('//example.com/a.jpg') == ''
    assert image_url('/api/products/images/assets/../../secrets') == ''


class CanadaItemsRemote(ItemsRemote):
    def __init__(self):
        super().__init__()
        self.currency = 'CAD'
        self.price_error = False

    def item(self):
        return super().item() | {'price': {'currency': self.currency, 'amount': self.price}}

    @property
    def writes(self):
        return [r for r in self.calls if r.method in ('PUT', 'DELETE') or (r.method == 'POST' and r.url.path == '/v3/price')]

    def handler(self, r):
        assert r.headers['WM_MARKET'] == 'ca'
        assert r.headers['WM_GLOBAL_VERSION'] == '3.1'
        assert r.url.path not in ('/v3/settings/partnerprofile', '/v3/settings/shipping/shipnodes', '/v3/items/walmart/search')
        if r.url.path == '/v3/price':
            self.calls.append(r)
            assert r.method == 'POST'
            payload = json.loads(r.content)
            assert payload['MPItemFeedHeader'] == {'mart': 'WALMART_CA', 'locale': ['en', 'fr'],
                'version': '5.0.20250801-18_47_55-api', 'businessUnit': 'WALMART_CA', 'tenant': 'WALMART_CA'}
            assert payload['MPItem'] == [{'Price': {'sku': 'REMOTE-ONLY', 'price': 30}}]
            if self.timeout: raise httpx.ReadTimeout('PRIVATE', request=r)
            if self.price_error:
                return httpx.Response(200, json={'itemsFailed': 1, 'itemDetails': {'itemIngestionStatus': [
                    {'sku': 'REMOTE-ONLY', 'ingestionStatus': 'DATA_ERROR', 'ingestionErrors': {'ingestionError': [{'description': 'Invalid selling price'}]}}]}})
            if not self.delay: self.price = payload['MPItem'][0]['Price']['price']
            return httpx.Response(200, json={'itemsSucceeded': 1})
        if r.url.path == '/v3/inventory':
            self.calls.append(r)
            assert r.url.params['sku'] == 'REMOTE-ONLY' and 'shipNode' not in r.url.params
            if r.method == 'PUT': self.quantity = json.loads(r.content)['quantity']['amount']
            return httpx.Response(200, json={'sku': 'REMOTE-ONLY', 'quantity': {'unit': 'EACH', 'amount': self.quantity}})
        return super().handler(r)


@pytest.fixture
def ca_offers(tmp_path, vault):
    from test_walmart_accounts import INPUT
    app = create_app(tmp_path / 'ca-offers.db')
    c = TestClient(app)
    r = CanadaItemsRemote().install(app)
    sid = c.get('/api/listing-stores').json()[0]['id']
    preview = c.post('/api/walmart/accounts/preview', json={'store_id': sid, 'marketplace': 'CA', **INPUT})
    assert preview.status_code == 200, preview.text
    bound = c.post(f'/api/walmart/accounts/{sid}/bind', json={'verification_id': preview.json()['verification_id']})
    assert bound.status_code == 200, bound.text
    app.state.walmart_publisher = WalmartPublishingClient(httpx.MockTransport(r.handler))
    page = c.get(f'/api/store-items/{sid}')
    assert page.status_code == 200, page.text
    return c, r, sid, page.json()


def ca_change(page, **kwargs):
    return dict(request_id=str(uuid.uuid4()), partner=page['partner'], revision=page['revision'],
                sku='REMOTE-ONLY', kind='price', expected='25', target='30') | kwargs


def test_canada_store_visible_remote_items_currency_default_inventory_and_images(ca_offers):
    from catalog.walmart_canada import DEFAULT_NODE
    c, r, sid, page = ca_offers
    assert c.get('/api/store-items').json()[0]['marketplace'] == 'CA'
    assert page['marketplace'] == 'CA' and page['currency'] == 'CAD'
    assert page['items'][0]['currency'] == 'CAD'
    assert c.get('/api/products').json()['total'] == 0
    assert c.get(f'/api/store-items/{sid}/history').json() == []
    assert c.get(f'/api/store-items/{sid}/nodes').json()[0]['id'] == DEFAULT_NODE
    assert c.get(f'/api/store-items/{sid}/stock', params={'sku': 'REMOTE-ONLY', 'node': DEFAULT_NODE}).json()['quantity'] == '7'
    image = c.get(f'/api/store-items/{sid}/image', params={'sku': 'REMOTE-ONLY', 'partner': page['partner'], 'revision': page['revision']})
    assert image.status_code == 200 and image.json()['image_status'] == 'missing'
    assert not r.writes


@pytest.mark.parametrize('kind', ['price', 'stock', 'retire'])
def test_canada_manual_write_and_readback_are_market_scoped(ca_offers, kind):
    from catalog.walmart_canada import DEFAULT_NODE
    c, r, sid, page = ca_offers
    changes = {'price': {}, 'stock': {'kind': 'stock', 'expected': '7', 'target': '0', 'node': DEFAULT_NODE},
               'retire': {'kind': 'retire', 'expected': 'ACTIVE', 'target': 'RETIRED', 'confirm_sku': 'REMOTE-ONLY'}}
    data = ca_change(page, **changes[kind])
    url = f'/api/store-items/{sid}/changes'
    response = c.post(url, json=data)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'verified'
    assert c.post(url, json=data).json()['status'] == 'verified' and len(r.writes) == 1
    assert c.get(f'/api/store-items/{sid}/history').json()[0]['kind'] == kind


@pytest.mark.parametrize('fault', ['currency', 'scope', 'timeout', 'price_error'])
def test_canada_write_guards_and_unknown_results(ca_offers, fault):
    c, r, sid, page = ca_offers
    if fault == 'currency': r.currency = 'USD'
    elif fault == 'scope': r.scopes['price'] = 'view_only'
    else: setattr(r, fault, True)
    data = ca_change(page)
    url = f'/api/store-items/{sid}/changes'
    result = c.post(url, json=data)
    if fault in ('currency', 'scope'):
        assert result.status_code == 502 and not r.writes
    else:
        assert result.json()['status'] == 'pending'
        if fault == 'price_error': assert 'Invalid selling price' in result.json()['message']
        assert c.post(url, json=data).json()['status'] == 'pending'
        assert len(r.writes) == 1
