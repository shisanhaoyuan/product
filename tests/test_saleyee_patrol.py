"""No live supplier or marketplace calls; patrols operate on isolated catalogs."""
import copy
import json

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.giga import sync_lease
from catalog.giga_patrol import run_patrol
from catalog.saleyee import SaleyeeSettings, SaleyeeError, SyncInput, sync_page
from catalog.walmart_publishing_client import WalmartPublishingClient
from test_saleyee import settings, FakeClient, actual_quote_fixture
from test_giga_walmart_sync import OfferRemote, actions
from test_prelisting import create, inputs
from test_walmart_accounts import vault, Remote, bind
from test_walmart_publishing import ready, send
from test_auth import team, login, ORIGIN


def seeded(tmp_path, settings, fake=None):
    app = create_app(tmp_path/'saleyee-patrol.db')
    fake = fake or FakeClient()
    sync_page(app.state.catalog, settings, SyncInput(), client=fake)
    fake.calls.clear()
    return TestClient(app), app.state.catalog, fake


def raw(catalog, sku='S1'):
    with catalog.connect() as db:
        return json.loads(db.execute('SELECT data_json FROM listings WHERE sku=?', (sku,)).fetchone()[0])


def change(fake, price='80', stock=0, index=0):
    fake.prices[index]['WarehousePriceList'][0]['SellingPrice'] = price
    fake.stock[index]['ProductInventorySiteList'][0]['ProductInventoryList'][0]['Qty'] = stock


def test_update_only_existing_price_stock_and_idempotent(tmp_path, settings):
    c, catalog, fake = seeded(tmp_path, settings)
    before = raw(catalog)
    pid = catalog.find(source='Saleyee', q='S1')['items'][0]['id']
    p = catalog.get(pid)
    assert c.patch('/api/products/'+pid+'/edits', json={
        'expected_revision':p['local_edit']['revision'], 'field':'stock', 'value':'10'}).status_code == 200
    change(fake)
    run = run_patrol(catalog, settings, fake)
    assert run['status'] == 'completed' and run['checked'] == 2
    assert run['price_changed'] == run['stock_changed'] == 1
    assert [op for op, _ in fake.calls] == ['price', 'stock']
    after = raw(catalog)
    assert after['price'] == '80' and after['stock'] == '0'
    assert catalog.get(pid)['stock'] == '10'  # Manual overlay survives.
    for k in before.keys() - {'price', 'stock', 'source_facts', 'observed_at'}:
        assert before[k] == after[k]
    assert before['source_facts']['saleyee']['detail'] == after['source_facts']['saleyee']['detail']
    detail = c.get('/api/integrations/saleyee/patrol/'+run['id']).json()
    item = next(i for i in detail['items'] if i['sku'] == 'S1')
    assert item['before']['price'] == '60' and item['after']['price'] == '80'
    assert item['after']['quote'] == ['US', 'US-W', 'L1']
    assert c.get('/api/integrations/giga/patrol/'+run['id']).status_code == 404
    history = len(catalog.get(pid)['history'])
    assert run_patrol(catalog, settings, fake)['changed'] == 0
    assert len(catalog.get(pid)['history']) == history


@pytest.mark.parametrize('failure', ['price', 'stock'])
def test_endpoint_failures_are_independent(tmp_path, settings, failure):
    _, catalog, fake = seeded(tmp_path, settings)
    change(fake)
    original = fake.query
    def query(op, body):
        if op == failure:
            raise SaleyeeError('SECRET TOKEN')
        return original(op, body)
    fake.query = query
    run = run_patrol(catalog, settings, fake)
    assert run['status'] == 'partial'
    data = raw(catalog)
    assert data['price'] == ('60' if failure == 'price' else '80')
    assert data['stock'] == ('25' if failure == 'stock' else '0')
    with catalog.connect() as db:
        assert 'SECRET TOKEN' not in str([dict(r) for r in db.execute('SELECT * FROM giga_patrol_items')])


@pytest.mark.parametrize('failure', ['missing', 'duplicate', 'wrong', 'null', 'negative', 'wrong_warehouse', 'wrong_site'])
def test_bad_stock_never_clears_saved_quantity(tmp_path, settings, failure):
    _, catalog, fake = seeded(tmp_path, settings)
    change(fake)
    row = fake.stock[0]
    region = row['ProductInventorySiteList'][0]['ProductInventoryList'][0]
    if failure == 'missing': fake.stock = fake.stock[1:]
    if failure == 'duplicate': fake.stock.append(copy.deepcopy(row))
    if failure == 'wrong': row['Sku'] = 'WRONG'
    if failure == 'null': region['Qty'] = None
    if failure == 'negative': region['Qty'] = -1
    if failure == 'wrong_warehouse': region['StockCode'] = 'OTHER'
    if failure == 'wrong_site': row['ProductInventorySiteList'][0]['SiteHosts'] = 'OTHER'
    run = run_patrol(catalog, settings, fake)
    assert run['status'] == 'partial'
    assert raw(catalog)['stock'] == '25'


@pytest.mark.parametrize('failure', ['NaN', 'other_quote', 'ambiguous', 'EUR'])
def test_invalid_price_does_not_block_valid_zero_stock(tmp_path, settings, failure):
    _, catalog, fake = seeded(tmp_path, settings)
    change(fake)
    offer = fake.prices[0]['WarehousePriceList'][0]
    if failure == 'NaN': offer['SellingPrice'] = 'NaN'
    if failure == 'other_quote': offer['LogisticsProductCode'] = 'L2'
    if failure == 'ambiguous': fake.prices[0]['WarehousePriceList'].append(copy.deepcopy(offer))
    if failure == 'EUR': offer['CurrencyCode'] = 'EUR'
    assert run_patrol(catalog, settings, fake)['status'] == 'partial'
    assert raw(catalog)['price'] == '60' and raw(catalog)['stock'] == '0'


def test_batch_limit_country_evidence_and_account_lock(tmp_path, settings, monkeypatch):
    c, catalog, fake = seeded(tmp_path, settings, FakeClient(tuple(f'S{i}' for i in range(31))))
    assert run_patrol(catalog, settings, fake)['checked'] == 31
    assert [len(b['SkuList']) for _, b in fake.calls] == [30, 30, 1, 1]
    with sync_lease(catalog, settings.namespace):
        assert run_patrol(catalog, settings, fake)['checked'] == 0
    with sync_lease(catalog, settings.namespace+'.patrol'):
        with pytest.raises(HTTPException): run_patrol(catalog, settings, fake)
    monkeypatch.setenv('CATALOG_SALEYEE_ACCOUNT', 'other')
    assert run_patrol(catalog, SaleyeeSettings(), fake)['total'] == 0


def test_null_destination_and_stock_failure_preserves_price(tmp_path, settings):
    _, catalog, fake = seeded(tmp_path, settings, actual_quote_fixture())
    change(fake, '150', 20)
    assert run_patrol(catalog, settings, fake)['status'] == 'completed'
    assert raw(catalog, '65567143')['price'] == '150'


def test_stale_lease_blocks_source_write(tmp_path, settings):
    _, catalog, fake = seeded(tmp_path, settings)
    change(fake)
    original = fake.query
    def query(op, body):
        with catalog.connect() as db:
            db.execute('UPDATE integration_leases SET expires_at=0 WHERE name=?', (settings.namespace,))
        return original(op, body)
    fake.query = query
    assert run_patrol(catalog, settings, fake)['status'] == 'failed'
    assert raw(catalog)['price'] == '60'


def test_history_auth_and_account_isolation(team, settings, monkeypatch):
    outside = TestClient(team, base_url=ORIGIN)
    assert outside.get('/api/integrations/saleyee/patrol').status_code == 401
    run = run_patrol(team.state.catalog, settings, FakeClient())
    viewer, _ = login(team, 'viewer')
    assert viewer.get('/api/integrations/saleyee/patrol/'+run['id']).status_code == 200
    monkeypatch.setenv('CATALOG_SALEYEE_ACCOUNT', 'other')
    assert viewer.get('/api/integrations/saleyee/patrol/'+run['id']).status_code == 404


@pytest.fixture
def live(tmp_path, settings, vault):
    c, catalog, fake = seeded(tmp_path, settings, FakeClient(('S1',)))
    b, _ = create(c, inputs(c, filters={'source':'Saleyee'}, multiplier='1.4', brand='SupplierBrand'))
    Remote().install(c.app); bind(c, b['preview']['store']['id'])
    remote = OfferRemote()
    gateway = WalmartPublishingClient(httpx.MockTransport(remote.handler))
    c.app.state.walmart_publisher = gateway
    base = f"/api/prelisting/batches/{b['id']}/walmart"
    doc = c.post(base+'/prepare').json()
    for row in doc['rows']:
        row['visible'].pop('productSecondaryImageURL', None)
        row['identification'] = dict(type='EAN', value='9780306406157', mode='identifier', submission_id='')
    assert c.patch(base, json={k:doc[k] for k in ('revision','version','rows')}).status_code == 200
    env = c, catalog, fake, settings, b, base, remote
    doc = ready(env)
    attempt = send(env, doc)
    result = c.post(base+f"/attempts/{attempt['id']}/refresh")
    assert result.status_code == 200 and all(r['status'] == 'published' for r in result.json()['results'].values())
    remote.calls.clear()
    return env, gateway, doc, attempt


def test_walmart_price_zero_and_repeat(live):
    env, gateway, doc, attempt = live
    change(env[2])
    run = run_patrol(env[1], env[3], env[2], publisher=gateway)
    assert run['status'] == 'completed'
    changed = [a for a in actions(env, run) if a['status'] == 'verified']
    assert {a['kind'] for a in changed} == {'price','stock'}
    assert next(a['target'] for a in changed if a['kind'] == 'price') == '112.00'
    assert len(env[6].puts) == 2 and not env[6].posts
    env[6].calls.clear()
    run_patrol(env[1], env[3], env[2], publisher=gateway)
    assert not env[6].puts
    assert env[0].get(env[5]).json()['rows'] == doc['rows']


@pytest.mark.parametrize('mutation', ['quote', 'namespace', 'processing', 'legacy'])
def test_walmart_identity_and_legacy_basis(live, mutation):
    env, gateway, doc, attempt = live
    with env[1].connect() as db:
        r = db.execute('SELECT rows_json,results_json FROM walmart_listing_attempts WHERE id=?', (attempt['id'],)).fetchone()
        rows = json.loads(r['rows_json']); results = json.loads(r['results_json'])
        if mutation == 'quote': rows[0]['patrol_basis']['quote'][1] = 'OTHER'
        if mutation == 'namespace': rows[0]['patrol_basis']['namespace'] = 'other'
        if mutation == 'processing':
            for value in results.values(): value['status'] = 'processing'
        if mutation == 'legacy': rows[0].pop('patrol_basis')
        db.execute('UPDATE walmart_listing_attempts SET rows_json=?,results_json=? WHERE id=?',
                   (json.dumps(rows), json.dumps(results), attempt['id']))
    change(env[2])
    run_patrol(env[1], env[3], env[2], publisher=gateway)
    assert len(env[6].puts) == (2 if mutation == 'legacy' else 0)


def test_walmart_uncertain_result_readback_before_retry(live):
    env, gateway, *_ = live
    change(env[2]); env[6].delay = True; env[6].timeout = True
    run = run_patrol(env[1], env[3], env[2], publisher=gateway)
    assert run['status'] == 'partial'
    env[6].calls.clear()
    run_patrol(env[1], env[3], env[2], publisher=gateway)
    assert not env[6].puts


def test_weekly_runner_continues_after_other_source_failure(monkeypatch, capsys, tmp_path, settings):
    from catalog import giga_patrol
    monkeypatch.setenv('CATALOG_DB', str(tmp_path/'scheduler.db'))
    monkeypatch.setenv('CATALOG_GIGA_CLIENT_ID', 'fake')
    monkeypatch.setenv('CATALOG_GIGA_CLIENT_SECRET', 'fake')
    monkeypatch.setattr('sys.argv', ['patrol'])
    seen = []
    def fake_run(catalog, config):
        seen.append(type(config).__name__)
        if isinstance(config, SaleyeeSettings):
            return {'status':'completed'}
        raise HTTPException(409, 'running')
    monkeypatch.setattr(giga_patrol, 'run_patrol', fake_run)
    assert giga_patrol.main() == 1
    assert seen == ['GigaSettings', 'SaleyeeSettings']
    seen.clear()
    monkeypatch.setattr('sys.argv', ['patrol', '--source', 'saleyee'])
    assert giga_patrol.main() == 0 and seen == ['SaleyeeSettings']


def test_multiple_quotes_remain_pinned_to_selected_logistics(tmp_path, settings):
    _, catalog, fake = seeded(tmp_path, settings)
    extra = copy.deepcopy(fake.prices[0]['WarehousePriceList'][0])
    extra.update(LogisticsProductCode='L2', SellingPrice='1')
    fake.prices[0]['WarehousePriceList'].insert(0, extra)
    fake.prices[0]['WarehousePriceList'][1]['SellingPrice'] = '85'
    assert run_patrol(catalog, settings, fake)['status'] == 'completed'
    data = raw(catalog)
    assert data['price'] == '85'
    assert data['source_facts']['selected_offer']['LogisticsProductCode'] == 'L1'
