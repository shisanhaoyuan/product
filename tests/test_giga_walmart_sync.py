"""All Marketplace I/O is mocked; these tests never update live offers."""
import copy
import json
from decimal import Decimal

import httpx
import pytest

from catalog.giga import GigaError
from catalog.giga_patrol import run_patrol
from catalog.walmart_publishing_client import WalmartPublishingClient
from test_giga import settings
from test_giga_prelisting import warehouse, plan
from test_prelisting import create
from test_walmart_accounts import vault, Remote, bind
from test_walmart_publishing import publishing, ready, send, PublisherRemote


class OfferRemote(PublisherRemote):
    def __init__(self):
        super().__init__()
        self.prices = {}
        self.permissions = {'item': 'full_access', 'price': 'full_access', 'inventory': 'full_access'}
        self.delay = False
        self.timeout = False
        self.before_read = None
        self.bad_sku = False
        self.currency = 'USD'

    @property
    def puts(self):
        return [r for r in self.calls if r.method == 'PUT']

    def handler(self, request):
        path = request.url.path
        if path == '/v3/token/detail':
            self.calls.append(request)
            return httpx.Response(200, json={'is_valid': True, 'scopes': self.permissions})
        if path == '/v3/price':
            self.calls.append(request)
            payload = json.loads(request.content)
            assert set(payload) == {'sku', 'pricing'}
            price = payload['pricing'][0]
            assert price['currentPriceType'] == 'BASE' and price['currentPrice']['currency'] == 'USD'
            if not self.delay:
                self.prices[payload['sku']] = price['currentPrice']['amount']
            if self.timeout:
                raise httpx.ReadTimeout('SECRET-MUST-NOT-LEAK', request=request)
            return httpx.Response(200, json={'sku': payload['sku']})
        if path.startswith('/v3/items/') and path not in ('/v3/items/taxonomy', '/v3/items/spec'):
            self.calls.append(request)
            if self.before_read:
                fn, self.before_read = self.before_read, None
                fn()
            sku = path.rsplit('/', 1)[-1]
            return httpx.Response(200, json={'ItemResponse': [{
                'sku': 'wrong' if self.bad_sku else sku, 'publishedStatus': 'PUBLISHED',
                'price': {'amount': self.prices.get(sku, 79.73), 'currency': self.currency}}]})
        return super().handler(request)


@pytest.fixture
def live_offers(publishing):
    c, catalog, fake, config, batch, base, old = publishing
    remote = OfferRemote()
    gateway = WalmartPublishingClient(httpx.MockTransport(remote.handler))
    c.app.state.walmart_publisher = gateway
    env = c, catalog, fake, config, batch, base, remote
    doc = ready(env)
    attempt = send(env, doc)
    result = c.post(base + f"/attempts/{attempt['id']}/refresh")
    assert result.status_code == 200, result.text
    assert all(r['status'] == 'published' for r in result.json()['results'].values())
    remote.calls.clear()
    return env, gateway, doc, attempt


def actions(env, run):
    with env[1].connect() as db:
        return [json.loads(r[0]) for r in db.execute('SELECT details_json FROM giga_patrol_actions WHERE run_id=? ORDER BY kind,platform_sku', (run['id'],))]


def patrol(live):
    env, gateway, *_ = live
    return run_patrol(env[1], env[3], env[2], publisher=gateway)


def raise_and_zero(env):
    env[2].prices[0]['price'] = '80'
    env[2].stock[0]['sellerInventoryInfo']['sellerAvailableInventory'] = 0


def test_direct_updates_detail_and_idempotence(live_offers):
    env, _, doc, _ = live_offers
    c, catalog, fake, _, _, _, remote = env
    before_rows = copy.deepcopy(doc['rows'])
    raise_and_zero(env)
    run = patrol(live_offers)
    assert run['status'] == 'completed'
    changed = [a for a in actions(env, run) if a['status'] == 'verified']
    assert {a['kind'] for a in changed} == {'price', 'stock'}
    assert next(a for a in changed if a['kind'] == 'price')['target'] == '94.68'
    assert next(a for a in changed if a['kind'] == 'price')['before'] == '79.73'
    assert len(remote.puts) == 2 and not remote.posts
    detail = c.get('/api/integrations/giga/patrol/' + run['id']).json()
    assert detail['run']['actions']['verified'] == 2
    assert sum(len(i['actions']) for i in detail['items']) == 4
    assert c.get(env[5]).json()['rows'] == before_rows
    remote.calls.clear()
    repeat = patrol(live_offers)
    assert not remote.puts and repeat['changed'] == 0
    assert not any(a['status'] == 'verified' for a in actions(env, repeat))


@pytest.mark.parametrize('failed', ['price', 'stock'])
def test_source_failure_independent_and_stale_zero_not_used(live_offers, failed):
    env, *_ = live_offers
    raise_and_zero(env)
    if failed == 'stock':
        # A prior valid zero may remain locally, but failed current evidence is not authority.
        patrol(live_offers)
        env[6].stock = {r['platform_sku']: 10 for r in live_offers[2]['rows']}
        env[2].prices[0]['price'] = '90'
    original = env[2].query
    def query(op, body):
        if op == failed:
            raise GigaError('secret')
        return original(op, body)
    env[2].query = query
    env[6].calls.clear()
    run = patrol(live_offers)
    assert run['status'] == 'partial'
    assert len(env[6].puts) == 1
    assert env[6].puts[0].url.path == ('/v3/inventory' if failed == 'price' else '/v3/price')


@pytest.mark.parametrize('problem', ['shipping', 'range', 'currency', 'permission', 'bad_sku', 'remote_currency'])
def test_bad_price_does_not_block_inventory(live_offers, problem):
    env, *_ = live_offers
    raise_and_zero(env)
    if problem == 'shipping': env[2].prices[0]['shippingFee'] = None
    if problem == 'range': env[2].prices[0]['shippingFeeRange'] = {'minAmount': '1', 'maxAmount': '5'}
    if problem == 'currency': env[2].prices[0]['currency'] = 'CAD'
    if problem == 'permission': env[6].permissions['price'] = 'view_only'
    if problem == 'bad_sku': env[6].bad_sku = True
    if problem == 'remote_currency': env[6].currency = 'CAD'
    patrol(live_offers)
    assert [r.url.path for r in env[6].puts] == ['/v3/inventory']


def test_current_higher_price_and_supplier_reduction_never_lower(live_offers):
    env, _, doc, _ = live_offers
    raise_and_zero(env)
    for row in doc['rows']: env[6].prices[row['platform_sku']] = 150
    patrol(live_offers)
    assert not any(r.url.path == '/v3/price' for r in env[6].puts)
    env[2].prices[0]['price'] = '10'
    env[6].calls.clear()
    patrol(live_offers)
    assert not env[6].puts


@pytest.mark.parametrize('timeout', [True, False])
def test_uncertain_write_readback_no_blind_retry(live_offers, timeout):
    env, *_ = live_offers
    raise_and_zero(env)
    env[6].timeout = timeout
    env[6].delay = True
    first = patrol(live_offers)
    price = next(a for a in actions(env, first) if a['kind'] == 'price' and a['source_sku'] == 'W1')
    assert price['status'] == 'pending'
    assert 'SECRET' not in json.dumps(price)
    env[6].calls.clear()
    second = patrol(live_offers)
    assert not env[6].puts
    assert any(a['status'] == 'blocked' for a in actions(env, second))
    env[6].prices[price['platform_sku']] = Decimal(price['target']).__float__()
    third = patrol(live_offers)
    assert not env[6].puts
    assert next(a for a in actions(env, first) if a['id'] == price['id'])['status'] == 'verified'
    assert third['status'] == 'completed'


def test_failed_permission_retries_next_week_without_double_multiplier(live_offers):
    env, *_ = live_offers
    env[2].prices[0]['price'] = '80'
    env[6].permissions['price'] = 'view_only'
    assert patrol(live_offers)['status'] == 'partial'
    env[6].permissions['price'] = 'full_access'
    result = patrol(live_offers)
    assert result['status'] == 'completed'
    assert [json.loads(r.content)['pricing'][0]['currentPrice']['amount'] for r in env[6].puts] == [94.68]


@pytest.mark.parametrize('mutation', ['claim', 'binding', 'source', 'sku_mapping'])
def test_final_ownership_guard(live_offers, mutation):
    env, _, doc, attempt = live_offers
    env[2].prices[0]['price'] = '80'
    row = next(r for r in doc['rows'] if r['source_sku'] == 'W1')
    def change():
        with env[1].connect() as db:
            if mutation == 'claim': db.execute('DELETE FROM walmart_listing_claims WHERE platform_sku=?', (row['platform_sku'],))
            elif mutation == 'binding': db.execute('UPDATE walmart_accounts SET revision=revision+1 WHERE store_id=?', (doc['store']['id'],))
            elif mutation == 'sku_mapping': db.execute('DELETE FROM platform_skus WHERE platform_sku=?', (row['platform_sku'],))
            else:
                data = json.loads(db.execute('SELECT data_json FROM listings WHERE id=?', (row['listing_id'],)).fetchone()[0])
                data['price'] = '1'
                db.execute('UPDATE listings SET data_json=? WHERE id=?', (json.dumps(data), row['listing_id']))
    env[6].before_read = change
    run = patrol(live_offers)
    assert not env[6].puts and run['status'] == 'partial'


def test_two_shops_use_own_sku_multiplier_and_node(live_offers):
    env, first_gateway, doc, _ = live_offers
    c, catalog, fake, config, _, _, first = env
    store = next(s for s in c.get('/api/listing-stores').json() if s['name'] == 'MYZ-L-18')
    payload = plan(c, store_id=store['id']); payload['multiplier'] = '1.4'
    second_batch, _ = create(c, payload)
    verifier = Remote().install(c.app); verifier.partner = '100010'
    bind(c, store['id'])
    second = OfferRemote(); second.partner = '100010'; second.node = 'node-18'
    second_gateway = WalmartPublishingClient(httpx.MockTransport(second.handler))
    c.app.state.walmart_publisher = second_gateway
    base = f"/api/prelisting/batches/{second_batch['id']}/walmart"
    assert c.post(base + '/prepare').status_code == 200
    other = c, catalog, fake, config, second_batch, base, second
    other_doc = ready(other); submitted = send(other, other_doc)
    assert c.post(base + f"/attempts/{submitted['id']}/refresh").status_code == 200
    second.calls.clear()
    class Gateway:
        def session(self, credentials, partner_id):
            return (first_gateway if partner_id == first.partner else second_gateway).session(credentials, partner_id)
    raise_and_zero(env)
    run = run_patrol(catalog, config, fake, publisher=Gateway())
    assert run['status'] == 'completed'
    results = [a for a in actions(env, run) if a['status'] == 'verified']
    assert len(results) == 4
    assert {a['target'] for a in results if a['kind'] == 'price'} == {'94.68', '115.26'}
    assert len({a['platform_sku'] for a in results}) == 2
    assert {a['node'] for a in results} == {'node-17', 'node-18'}
    assert not first.posts and not second.posts


def test_inventory_permission_failure_does_not_block_price(live_offers):
    env, *_ = live_offers
    raise_and_zero(env)
    env[6].permissions['inventory'] = 'view_only'
    run = patrol(live_offers)
    assert run['status'] == 'partial'
    assert [r.url.path for r in env[6].puts] == ['/v3/price']


def test_two_price_increases_use_frozen_multiplier(live_offers):
    env, *_ = live_offers
    env[2].prices[0]['price'] = '80'
    patrol(live_offers)
    env[2].prices[0]['price'] = '90'
    env[2].prices[0]['shippingFee'] = '3'
    patrol(live_offers)
    prices = [json.loads(r.content)['pricing'][0]['currentPrice']['amount'] for r in env[6].puts]
    assert prices == [94.68, 106.95]


def test_pending_request_is_reconciled_even_after_source_price_drops(live_offers):
    env, *_ = live_offers
    env[2].prices[0]['price'] = '80'
    env[6].delay = True
    first = patrol(live_offers)
    price = next(a for a in actions(env, first) if a['source_sku'] == 'W1' and a['kind'] == 'price')
    assert price['status'] == 'pending'
    env[6].prices[price['platform_sku']] = 94.68
    env[2].prices[0]['price'] = '30'
    env[6].calls.clear()
    patrol(live_offers)
    assert not env[6].puts
    assert next(a for a in actions(env, first) if a['id'] == price['id'])['status'] == 'verified'


def test_legacy_attempt_uses_immutable_batch_multiplier(live_offers):
    env, _, _, attempt = live_offers
    with env[1].connect() as db:
        rows = json.loads(db.execute('SELECT rows_json FROM walmart_listing_attempts WHERE id=?', (attempt['id'],)).fetchone()[0])
        for row in rows: row.pop('patrol_basis')
        db.execute('UPDATE walmart_listing_attempts SET rows_json=? WHERE id=?', (json.dumps(rows), attempt['id']))
    env[2].prices[0]['price'] = '80'
    run = patrol(live_offers)
    assert next(a for a in actions(env, run) if a['status'] == 'verified')['target'] == '94.68'


@pytest.mark.parametrize('status', ['processing', 'failed', 'unpublished'])
def test_only_confirmed_published_items_are_updated(live_offers, status):
    env, _, _, attempt = live_offers
    with env[1].connect() as db:
        results = json.loads(db.execute('SELECT results_json FROM walmart_listing_attempts WHERE id=?', (attempt['id'],)).fetchone()[0])
        for row in results.values(): row['status'] = status
        db.execute('UPDATE walmart_listing_attempts SET results_json=? WHERE id=?', (json.dumps(results), attempt['id']))
    raise_and_zero(env)
    run = patrol(live_offers)
    assert not env[6].calls and not actions(env, run)


def test_result_poll_lock_prevents_concurrent_update(live_offers):
    import time
    env, _, _, attempt = live_offers
    with env[1].connect() as db:
        db.execute('INSERT INTO walmart_listing_polls VALUES(?,?,?)', (attempt['id'], 'other-worker', int(time.time())))
    raise_and_zero(env)
    run = patrol(live_offers)
    assert run['status'] == 'partial' and not env[6].puts
    with env[1].connect() as db:
        assert db.execute('SELECT token FROM walmart_listing_polls WHERE attempt_id=?', (attempt['id'],)).fetchone()[0] == 'other-worker'
