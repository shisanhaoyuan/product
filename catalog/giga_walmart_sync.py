"""Audited, conservative updates of existing Walmart offers after a GIGA patrol.

Never submits an item feed. An ambiguous PUT is read back on later patrols and
blocks another PUT for that store/SKU/field until its outcome can be established.
"""
import json
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException

from . import platform_sku
from .credential_vault import decrypt, VaultError
from .database import write_lock
from .giga_presentation import number, summary
from .walmart_client import WalmartError
from .walmart_publishing import binding
from .walmart_publishing_client import WalmartPublishingClient


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS giga_patrol_evidence(
        run_id TEXT NOT NULL REFERENCES giga_patrol_runs(id),listing_id TEXT NOT NULL,
        evidence_json TEXT NOT NULL,PRIMARY KEY(run_id,listing_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS giga_patrol_actions(
        id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES giga_patrol_runs(id),
        listing_id TEXT NOT NULL,store_id INTEGER NOT NULL,platform_sku TEXT NOT NULL,
        kind TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,details_json TEXT NOT NULL,
        UNIQUE(run_id,listing_id,store_id,platform_sku,kind))''')
    db.execute('CREATE INDEX IF NOT EXISTS giga_actions_offer ON giga_patrol_actions(store_id,platform_sku,kind,created_at)')
    db.execute('INSERT INTO migrations VALUES(21) ON CONFLICT DO NOTHING')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def evidence(data, price_ok, stock_ok, before):
    info = summary(data.get('source_facts'))
    totals = info['estimated_totals']
    return dict(price_ok=price_ok, stock_ok=stock_ok, source=data,
                before_landed=before, landed=totals[0] if len(totals) == 1 else None)


def amounts(data):
    totals = summary(data.get('source_facts'))['estimated_totals']
    return totals[0] if len(totals) == 1 else None


def update(db, action, status, message):
    from .giga_patrol import now
    action.update(status=status, message=message)
    db.execute('UPDATE giga_patrol_actions SET status=?,updated_at=?,details_json=? WHERE id=?',
               (status, now(), encoded(action), action['id']))


def candidates(db, listing_id, source_sku):
    result = []
    for claim in db.execute('''SELECT a.*,c.platform_sku,b.settings_json,b.preview_json,s.name AS store_name
        FROM walmart_listing_claims c JOIN walmart_listing_attempts a ON a.id=c.attempt_id
        JOIN prelisting_batches b ON b.id=a.batch_id JOIN listing_stores s ON s.id=a.store_id
        JOIN prelisting_items pi ON pi.batch_id=a.batch_id
        WHERE pi.listing_id=? AND c.store_id=a.store_id AND c.batch_id=a.batch_id''', (listing_id,)):
        rows = [r for r in json.loads(claim['rows_json']) if r['platform_sku'] == claim['platform_sku']
                and r['listing_id'] == listing_id and r['source_sku'] == source_sku]
        outcome = json.loads(claim['results_json']).get(claim['platform_sku'], {})
        if len(rows) == 1 and outcome.get('status') == 'published':
            result.append((dict(claim), rows[0]))
    return result


def basis(claim, row):
    # New submissions freeze the exact basis on the attempt. Legacy batches have
    # immutable creation settings; source refreshes do not change the multiplier.
    if 'patrol_basis' in row:
        return row['patrol_basis']
    settings = json.loads(claim['settings_json'])
    preview = json.loads(claim['preview_json'])
    matches = [v for i in preview['items'] if i['listing_id'] == row['listing_id']
               and i['source'] == 'GigaB2B' for v in i['variants']
               if v['platform_sku'] == row['platform_sku'] and v['sku'] == row['source_sku']]
    return dict(multiplier=settings.get('multiplier'), landed=matches[0].get('base') if len(matches) == 1 else None)


def check_identity(db, action, ev, namespace, account_token, run_token, bound=None, poll_token=None):
    from .giga_patrol import assert_lease
    assert_lease(db, namespace, account_token)
    assert_lease(db, namespace + '.patrol', run_token)
    current = db.execute('SELECT namespace,sku,external_id,data_json FROM listings WHERE id=?', (action['listing_id'],)).fetchone()
    expected = ev['source']
    if (not current or current['namespace'] != namespace or current['sku'] != action['source_sku']
            or current['external_id'] != expected['external_id'] or json.loads(current['data_json']) != expected
            or expected['source'] != 'GigaB2B' or expected.get('asin') or expected.get('variants')):
        raise WalmartError('巡查后的来源或商品身份已改变，未继续写入')
    claim = db.execute('SELECT attempt_id FROM walmart_listing_claims WHERE store_id=? AND platform_sku=?',
                       (action['store_id'], action['platform_sku'])).fetchone()
    if not claim or claim['attempt_id'] != action['attempt_id']:
        raise WalmartError('商品提交归属已改变，未继续写入')
    if platform_sku.lookup(db, action['store_id'], 'GigaB2B', {'sku': action['source_sku']}) != action['platform_sku']:
        raise WalmartError('店铺与平台 SKU 映射不一致，未继续写入')
    _, current_bound = binding(db, action['store_id'], action['partner_id'])
    if bound and current_bound['revision'] != bound['revision']:
        raise WalmartError('店铺授权已改变，未继续写入')
    if poll_token:
        poll = db.execute('SELECT token FROM walmart_listing_polls WHERE attempt_id=?', (action['attempt_id'],)).fetchone()
        if not poll or poll['token'] != poll_token:
            raise WalmartError('店铺更新锁已失效，未继续写入')
        db.execute('UPDATE walmart_listing_polls SET started_at=? WHERE attempt_id=?', (int(time.time()), action['attempt_id']))
    return current_bound


def read_value(remote, action):
    sku = action['platform_sku']
    if action['kind'] == 'price':
        rows = remote.item(sku).get('ItemResponse')
        if not isinstance(rows, list) or len(rows) != 1 or rows[0].get('sku') != sku:
            raise WalmartError('售价回读 SKU 不一致，已停止')
        if rows[0].get('publishedStatus') != 'PUBLISHED':
            raise WalmartError('商品当前未处于已上架状态，未自动改价')
        price = rows[0].get('price') or {}
        value = number(price.get('amount'))
        if price.get('currency') != 'USD' or value is None:
            raise WalmartError('未读到有效 USD 售价，未自动改价')
        return str(value)
    stock = remote.inventory(sku, action['node'])
    quantity = stock.get('quantity') or {}
    if (stock.get('sku') != sku or type(quantity.get('amount')) is not int
            or quantity['amount'] < 0 or quantity.get('unit') != 'EACH'
            or stock.get('shipNode', action['node']) != action['node']):
        raise WalmartError('库存回读的 SKU、仓库或数量异常，已停止')
    return str(quantity['amount'])


def perform(catalog, action, ev, namespace, account_token, run_token, publisher):
    poll_token = None
    write_started = False
    try:
        with catalog.connect() as db:
            bound = check_identity(db, action, ev, namespace, account_token, run_token)
        with publisher.session(decrypt(action['store_id'], bound['encrypted_credentials']), action['partner_id']) as remote:
            with catalog.connect() as db:
                write_lock(db)
                check_identity(db, action, ev, namespace, account_token, run_token, bound)
                prior = db.execute('SELECT * FROM walmart_listing_polls WHERE attempt_id=?', (action['attempt_id'],)).fetchone()
                if prior and prior['started_at'] > time.time() - 180:
                    raise WalmartError('此商品批次正在查询或更新，下次巡查重试')
                poll_token = uuid.uuid4().hex
                db.execute('''INSERT INTO walmart_listing_polls VALUES(?,?,?) ON CONFLICT(attempt_id)
                    DO UPDATE SET token=excluded.token,started_at=excluded.started_at''',
                           (action['attempt_id'], poll_token, int(time.time())))
                unresolved = [dict(r) for r in db.execute('''SELECT * FROM giga_patrol_actions
                    WHERE store_id=? AND platform_sku=? AND kind=? AND status IN ('sending','pending') AND id<>?''',
                    (action['store_id'], action['platform_sku'], action['kind'], action['id']))]
            action['before'] = read_value(remote, action)
            # Reconcile ALL ambiguous earlier requests before considering another write.
            with catalog.connect() as db:
                write_lock(db)
                for old in unresolved:
                    detail = json.loads(old['details_json'])
                    same_owner = (detail['partner_id'] == action['partner_id'] and detail['node'] == action['node'])
                    reached = (Decimal(action['before']) >= Decimal(detail['target']) if action['kind'] == 'price'
                               else action['before'] == detail['target']) if detail.get('target') is not None else False
                    if same_owner and reached:
                        detail['after'] = action['before']
                        update(db, detail, 'verified', '后续巡查回读确认已达到目标')
                    else:
                        action['blocking_action'] = old['id']
                        update(db, action, 'blocked', '上次更新结果仍未确认；本次只回读，不重复提交，请在卖家中心核对')
                        return
            if action.get('skip_reason'):
                with catalog.connect() as db:
                    write_lock(db)
                    update(db, action, 'skipped', action['skip_reason'])
                return
            remote.require_scope('price' if action['kind'] == 'price' else 'inventory')
            with catalog.connect() as db:
                write_lock(db)
                check_identity(db, action, ev, namespace, account_token, run_token, bound, poll_token)
                already_at_target = (Decimal(action['before']) >= Decimal(action['target'])
                                     if action['kind'] == 'price' else action['before'] == '0')
                if already_at_target:
                    action['after'] = action['before']
                    update(db, action, 'unchanged', '当前售价已达到或高于计算目标，保留现价' if action['kind'] == 'price' else '当前库存已经为 0，无需重复写入')
                    return
                update(db, action, 'sending', '已记录更新意图，正在提交；中断后先回读，不直接重发')
            # Sending is durable. Hold the common write lock across this single PUT
            # so a local rebind/new claim cannot race the final ownership check.
            with catalog.connect() as db:
                write_lock(db)
                check_identity(db, action, ev, namespace, account_token, run_token, bound, poll_token)
                write_started = True
                if action['kind'] == 'price':
                    remote.set_price(action['platform_sku'], action['target'])
                else:
                    remote.set_inventory(action['platform_sku'], 0, action['node'])
            action['after'] = read_value(remote, action)
            with catalog.connect() as db:
                write_lock(db)
                matched = Decimal(action['after']) == Decimal(action['target'])
                update(db, action, 'verified' if matched else 'pending',
                       '沃尔玛回读确认修改成功' if matched else '接口已接收，但回读尚未达到目标；下次巡查只核对，不重复提交')
    except Exception as exc:
        if isinstance(exc, HTTPException):
            message = str(exc.detail)
        elif isinstance(exc, (WalmartError, VaultError)):
            message = str(exc)
        else:
            message = '店铺更新异常，请检查连接与配置；未确认结果不计成功'
        with catalog.connect() as db:
            write_lock(db)
            update(db, action, 'pending' if write_started else 'failed', message)
    finally:
        if poll_token:
            with catalog.connect() as db:
                write_lock(db)
                db.execute('DELETE FROM walmart_listing_polls WHERE attempt_id=? AND token=?', (action['attempt_id'], poll_token))


def sync_batch(catalog, run_id, namespace, targets, account_token, run_token, publisher=None):
    from .giga_patrol import now, renew_lease
    publisher = publisher or WalmartPublishingClient()
    for target in targets:
        with catalog.connect() as db:
            stored = db.execute('SELECT evidence_json FROM giga_patrol_evidence WHERE run_id=? AND listing_id=?', (run_id, target['id'])).fetchone()
            ev = json.loads(stored[0])
            offers = candidates(db, target['id'], target['sku'])
        for claim, row in offers:
            try:
                frozen = basis(claim, row)
            except (ValueError, KeyError, TypeError):
                frozen = {}
            for kind in ('price', 'stock'):
                renew_lease(catalog, namespace, account_token, seconds=900)
                renew_lease(catalog, namespace + '.patrol', run_token, seconds=900)
                action = dict(id=uuid.uuid4().hex, run_id=run_id, listing_id=target['id'], source_sku=target['sku'],
                              store_id=claim['store_id'], store_name=claim['store_name'], platform_sku=claim['platform_sku'],
                              attempt_id=claim['id'], batch_id=claim['batch_id'], partner_id=claim['partner_id'],
                              node=row.get('fulfillment_center_id'), kind=kind, multiplier=frozen.get('multiplier'),
                              source_price=ev['source'].get('price'), source_shipping=ev['source'].get('source_facts', {}).get('shipping'),
                              source_stock=ev['source'].get('stock'), landed=ev['landed'], target=None, before=None, after=None)
                reason = ''
                if kind == 'stock':
                    if not ev['stock_ok']:
                        reason = '本轮库存未核验成功，保留沃尔玛库存'
                    elif str(action['source_stock']) != '0':
                        reason = '供应商库存不为 0，不自动补库存或调整现有数量'
                    elif not action['node']:
                        reason = '缺少原上架仓库编号，无法自动清零'
                    else:
                        action['target'] = '0'
                else:
                    cost, multiplier = number(ev['landed']), number(frozen.get('multiplier'))
                    before, original = number(ev['before_landed']), number(frozen.get('landed'))
                    with catalog.connect() as db:
                        last = db.execute('''SELECT status,details_json FROM giga_patrol_actions WHERE store_id=? AND platform_sku=?
                            AND kind='price' AND status<>'skipped' ORDER BY created_at DESC,id DESC LIMIT 1''',
                            (action['store_id'], action['platform_sku'])).fetchone()
                    previous = json.loads(last['details_json']) if last else {}
                    retry = last and last['status'] in ('failed','pending','sending','blocked')
                    previous_cost = number(previous.get('landed'))
                    rise = cost is not None and ((before is not None and cost > before)
                            or (not last and original is not None and cost > original)
                            or (retry and previous_cost is not None and cost >= previous_cost))
                    if not ev['price_ok'] or ev['source'].get('source_facts', {}).get('currency') != 'USD':
                        reason = '本轮未核验有效 USD 来源价格，保留沃尔玛售价'
                    elif (cost is None or multiplier is None or not 0 < multiplier <= 100 or cost <= 0
                          or (number(action['source_price']) or Decimal(0)) <= 0):
                        reason = '来源物流费不确定或原上架倍率缺失，无法自动改价'
                    elif not rise:
                        reason = '来源含运费成本未上涨，不自动降价'
                    else:
                        action['target'] = str((cost * multiplier).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
                        if Decimal(action['target']) <= 0:
                            reason = '计算后的售价不是有效正数，未改价'
                with catalog.connect() as db:
                    write_lock(db)
                    pending = db.execute("SELECT 1 FROM giga_patrol_actions WHERE store_id=? AND platform_sku=? AND kind=? AND status IN ('sending','pending') LIMIT 1",
                                         (action['store_id'], action['platform_sku'], kind)).fetchone()
                    if reason and pending:
                        action['skip_reason'] = reason
                    stamp = now()
                    db.execute('INSERT INTO giga_patrol_actions VALUES(?,?,?,?,?,?,?,?,?,?)',
                               (action['id'],run_id,target['id'],action['store_id'],action['platform_sku'],kind,'queued',stamp,stamp,encoded(action)))
                    update(db, action, 'skipped' if reason else 'queued', reason or '等待核验店铺及当前值')
                if not reason or pending:
                    perform(catalog, action, ev, namespace, account_token, run_token, publisher)


def counts(db, run_id):
    return {r['status']: r['n'] for r in db.execute('SELECT status,COUNT(*) AS n FROM giga_patrol_actions WHERE run_id=? GROUP BY status', (run_id,))}
