"""Scheduled, source-only price/inventory refresh for existing GigaB2B SKUs.

Run with: python -m catalog.giga_patrol --env-file /etc/jiwu.env
No product discovery, publishing, multiplier, inventory cap, or credential output.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from .database import write_lock
from .giga import GigaClient, GigaError, GigaSettings, amount, sync_lease


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS giga_patrol_runs(
        id TEXT PRIMARY KEY,namespace TEXT NOT NULL,started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL,status TEXT NOT NULL,total INTEGER NOT NULL,
        checked INTEGER NOT NULL,changed INTEGER NOT NULL,failed INTEGER NOT NULL,
        price_changed INTEGER NOT NULL,stock_changed INTEGER NOT NULL,message TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS giga_patrol_items(
        run_id TEXT NOT NULL REFERENCES giga_patrol_runs(id),listing_id TEXT NOT NULL,
        sku TEXT NOT NULL,checked_at TEXT NOT NULL,status TEXT NOT NULL,
        before_json TEXT NOT NULL,after_json TEXT NOT NULL,errors_json TEXT NOT NULL,
        PRIMARY KEY(run_id,listing_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS giga_patrol_started ON giga_patrol_runs(namespace,started_at)')
    db.execute('INSERT INTO migrations VALUES(14) ON CONFLICT DO NOTHING')


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def values(data):
    facts = data.get('source_facts') or {}
    return dict(price=data.get('price'), shipping=facts.get('shipping'),
                currency=facts.get('currency'), stock=data.get('stock'),
                exclusive_price=(facts.get('giga', {}).get('price') or {}).get('exclusivePrice'),
                discounted_price=(facts.get('giga', {}).get('price') or {}).get('discountedPrice'))


def validate_prices(row):
    price = amount(row.get('price'))
    currency = row.get('currency')
    if price is None or not isinstance(currency, str) or not currency.strip():
        raise GigaError('未返回有效原价或币种，保留原价格')
    # An absent shipping fee remains unknown; never turn it into free shipping.
    shipping = amount(row.get('shippingFee'))
    for key in ('exclusivePrice', 'discountedPrice', 'remoteShippingFee'):
        amount(row.get(key))
    return price, shipping, currency.strip()


def validate_inventory(row):
    info = row.get('sellerInventoryInfo')
    quantity = info.get('sellerAvailableInventory') if isinstance(info, dict) else None
    if type(quantity) is not int or quantity < 0:
        raise GigaError('未返回有效可售库存，保留原库存')
    return quantity


def fetch_rows(client, operation, skus):
    label = '价格' if operation == 'price' else '库存'
    try:
        # Preserve one client's limiter across both endpoints and all batches.
        client.deadline = time.monotonic() + 60
        rows = client.query(operation, {'skus': skus})
        if not isinstance(rows, list):
            raise GigaError(f'{label}响应格式异常')
        indexed = {}
        for row in rows:
            sku = row.get('sku') if isinstance(row, dict) else None
            if not isinstance(sku, str) or sku not in skus or sku in indexed:
                raise GigaError(f'{label}返回重复或不匹配的 SKU')
            indexed[sku] = row
        # Missing individual rows are failures for that SKU only.
        return indexed, ''
    except GigaError:
        # Do not persist third-party payloads, credentials or transport exception reprs.
        return {}, f'{label}查询失败或返回 SKU 异常，请检查接口权限及连接；保留原值'
    except (ValueError, TypeError, KeyError):
        return {}, f'{label}响应格式异常，保留原值'


def assert_lease(db, name, token):
    lease = db.execute('SELECT token,expires_at FROM integration_leases WHERE name=?', (name,)).fetchone()
    if not lease or lease['token'] != token or lease['expires_at'] <= time.time():
        raise GigaError('巡查锁已失效，已停止写入，请重新巡查')


def renew_lease(catalog, name, token):
    with catalog.connect() as db:
        write_lock(db)
        assert_lease(db, name, token)
        db.execute('UPDATE integration_leases SET expires_at=? WHERE name=? AND token=?',
                   (time.time()+180, name, token))


def apply_batch(catalog, run_id, namespace, targets, price_rows, price_error,
                stock_rows, stock_error, account_token, run_token):
    checked_at = now()
    with catalog.connect() as db:
        write_lock(db)
        assert_lease(db, namespace, account_token)
        assert_lease(db, namespace + '.patrol', run_token)
        for target in targets:
            listing_id, sku = target['id'], target['sku']
            existing = db.execute('SELECT * FROM listings WHERE id=?', (listing_id,)).fetchone()
            errors = []
            before = after = {}
            changed_price = changed_stock = False
            if existing:
                data = json.loads(existing['data_json'])
                before = values(data)
                after = dict(before)
            if (not existing or existing['namespace'] != namespace or existing['sku'] != sku
                    or data.get('source') != 'GigaB2B' or data.get('asin')
                    or data.get('sku') != sku or data.get('external_id') != target['external_id']
                    or data.get('evidence_kind') != 'source_only' or data.get('variants')):
                errors.append('商品身份或结构发生变化，未写入')
                price_ok = stock_ok = False
            else:
                price_ok = stock_ok = False
                facts = data.setdefault('source_facts', {})
                giga = facts.setdefault('giga', {})
                price_row = price_rows.get(sku)
                stock_row = stock_rows.get(sku)
                if price_error or price_row is None:
                    errors.append(price_error or '未返回本 SKU 的价格，保留原价格')
                else:
                    try:
                        price, shipping, currency = validate_prices(price_row)
                        # Keep supplier identity and other source facts untouched.
                        previous = giga.get('price') or {}
                        incoming = {k: v for k, v in price_row.items() if k not in ('sku', 'sellerInfo', 'skuAvailable')}
                        price_facts = {k: v for k, v in previous.items() if k in ('sku', 'sellerInfo', 'skuAvailable')}
                        price_facts.update(incoming)
                        price_facts['sku'] = sku
                        changed_price = (data.get('price') != price or facts.get('shipping') != shipping
                                         or facts.get('currency') != currency or previous != price_facts)
                        data['price'] = price
                        facts.update(price=price, shipping=shipping, currency=currency)
                        giga['price'] = price_facts
                        price_ok = True
                    except (GigaError, TypeError, ValueError):
                        errors.append('价格数据缺失或格式异常，保留原价格')
                if stock_error or stock_row is None:
                    errors.append(stock_error or '未返回本 SKU 的库存，保留原库存')
                else:
                    try:
                        quantity = validate_inventory(stock_row)
                        changed_stock = (data.get('stock') != str(quantity) or giga.get('inventory') != stock_row)
                        data['stock'] = str(quantity)
                        facts['inventory'] = quantity
                        giga['inventory'] = stock_row
                        stock_ok = True
                    except (GigaError, TypeError, ValueError):
                        errors.append('未返回有效可售库存，保留原库存')
                after = values(data)
                if changed_price or changed_stock:
                    data['observed_at'] = checked_at
                    raw = encode(data)
                    digest = hashlib.sha256(raw.encode()).hexdigest()
                    db.execute('UPDATE listings SET data_json=?,digest=?,observed_at=? WHERE id=?',
                               (raw, digest, checked_at, listing_id))
                    db.execute('''INSERT INTO snapshots(listing_id,digest,observed_at,evidence_kind,data_json)
                        VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING''',
                               (listing_id, digest, checked_at, data['evidence_kind'], raw))
                    catalog.refresh(db, listing_id)
            status = 'ok' if price_ok and stock_ok else 'partial' if price_ok or stock_ok else 'failed'
            db.execute('INSERT INTO giga_patrol_items VALUES(?,?,?,?,?,?,?,?)',
                       (run_id, listing_id, sku, checked_at, status, encode(before), encode(after), encode(errors)))
            # Source write and its audit counters commit together.
            db.execute('''UPDATE giga_patrol_runs SET checked=checked+1,changed=changed+?,
                failed=failed+?,price_changed=price_changed+?,stock_changed=stock_changed+? WHERE id=?''',
                       (int(changed_price or changed_stock), int(bool(errors)),
                        int(changed_price), int(changed_stock), run_id))


def run_patrol(catalog, settings=None, client=None):
    settings = settings or GigaSettings()
    name = settings.namespace + '.patrol'
    with sync_lease(catalog, name) as run_token:
        run_id = uuid.uuid4().hex
        with catalog.connect() as db:
            write_lock(db)
            # Once this process owns the lease, unfinished older runs have no live owner.
            db.execute("UPDATE giga_patrol_runs SET status='interrupted',finished_at=?,message=? WHERE namespace=? AND status='running'",
                       (now(), '上次巡查中断；已保存记录保留，未检查项不算成功', settings.namespace))
            targets = [dict(r) for r in db.execute('''SELECT l.id,l.sku,l.external_id FROM listings l
                JOIN products p ON p.id=l.product_id WHERE l.namespace=? AND p.source='GigaB2B'
                ORDER BY l.id''', (settings.namespace,))]
            db.execute('INSERT INTO giga_patrol_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (run_id, settings.namespace, now(), '', 'running', len(targets), 0, 0, 0, 0, 0, ''))
        try:
            if not settings.configured:
                raise GigaError('尚未配置大建云仓 API，巡查未执行')
            client = client or GigaClient(settings)
            for start in range(0, len(targets), 100):
                renew_lease(catalog, name, run_token)
                batch = targets[start:start+100]
                # Shared with full sync, so its stale price/stock cannot race this write.
                with sync_lease(catalog, settings.namespace) as account_token:
                    skus = [r['sku'] for r in batch]
                    if len(skus) != len(set(skus)):
                        raise GigaError('本地 SKU 重复，已停止巡查')
                    prices, price_error = fetch_rows(client, 'price', skus)
                    stocks, stock_error = fetch_rows(client, 'stock', skus)
                    apply_batch(catalog, run_id, settings.namespace, batch, prices, price_error,
                                stocks, stock_error, account_token, run_token)
            with catalog.connect() as db:
                write_lock(db)
                row = db.execute('SELECT * FROM giga_patrol_runs WHERE id=?', (run_id,)).fetchone()
                status = 'partial' if row['failed'] else 'completed'
                message = '没有属于当前 API 账号的已入库商品' if not targets else ''
                db.execute('UPDATE giga_patrol_runs SET status=?,finished_at=?,message=? WHERE id=?',
                           (status, now(), message, run_id))
        except Exception as exc:
            message = (str(exc) if isinstance(exc, GigaError) else
                       '有其他同步正在运行，请稍后重试' if isinstance(exc, HTTPException) else
                       '巡查执行异常；已提交批次保留，未完成项请重新巡查')
            with catalog.connect() as db:
                write_lock(db)
                db.execute("UPDATE giga_patrol_runs SET status='failed',finished_at=?,message=? WHERE id=?",
                           (now(), message, run_id))
        with catalog.connect() as db:
            return dict(db.execute('SELECT * FROM giga_patrol_runs WHERE id=?', (run_id,)).fetchone())


def timer_status():
    result = dict(schedule='每周一 09:00（北京时间）', active=False, installed=False)
    if sys.platform != 'linux':
        return result
    try:
        proc = subprocess.run(['systemctl', 'show', 'jiwu-giga-patrol.timer',
                               '--property=LoadState,ActiveState,NextElapseUSecRealtime'],
                              capture_output=True, text=True, timeout=2)
        if proc.returncode == 0:
            fields = dict(line.split('=', 1) for line in proc.stdout.splitlines() if '=' in line)
            result.update(installed=fields.get('LoadState') == 'loaded',
                          active=fields.get('ActiveState') == 'active',
                          next_run=fields.get('NextElapseUSecRealtime', ''))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return result


def visible_run(db, row):
    result = dict(row)
    if result['status'] == 'running':
        lease = db.execute('SELECT expires_at FROM integration_leases WHERE name=?', (result['namespace'] + '.patrol',)).fetchone()
        if not lease or lease[0] <= time.time():
            result.update(status='interrupted', message='巡查进程已中断或失去心跳；未检查项不算成功')
    return result


def patrol_routes(catalog):
    router = APIRouter(prefix='/api/integrations/giga/patrol')

    @router.get('')
    def runs():
        settings = GigaSettings()
        with catalog.connect() as db:
            rows = [visible_run(db, r) for r in db.execute('SELECT * FROM giga_patrol_runs WHERE namespace=? ORDER BY started_at DESC,id DESC LIMIT 20', (settings.namespace,))]
        return dict(configured=settings.configured, timer=timer_status(), runs=rows)

    @router.get('/{run_id}')
    def details(run_id: str, page: int = Query(1, ge=1)):
        with catalog.connect() as db:
            run = db.execute('SELECT * FROM giga_patrol_runs WHERE id=? AND namespace=?',
                             (run_id, GigaSettings().namespace)).fetchone()
            if not run:
                raise HTTPException(404, '巡查记录不存在')
            run = visible_run(db, run)
            rows = [dict(r) for r in db.execute('SELECT * FROM giga_patrol_items WHERE run_id=? ORDER BY sku,listing_id LIMIT 100 OFFSET ?',
                                               (run_id, (page-1)*100))]
        for row in rows:
            for key in ('before', 'after', 'errors'):
                row[key] = json.loads(row.pop(key+'_json'))
        return dict(run=dict(run), items=rows, page=page, page_size=100)

    return router


def main():
    parser = argparse.ArgumentParser(description='大建云仓每周价格和库存巡查')
    parser.add_argument('--env-file', type=Path)
    args = parser.parse_args()
    if args.env_file:
        from .config import load_env_file
        load_env_file(args.env_file)
    from .db import Catalog
    path = os.environ.get('CATALOG_DATABASE_URL') or os.environ.get('CATALOG_DB', Path(__file__).resolve().parent.parent/'data/catalog.db')
    try:
        result = run_patrol(Catalog(path))
    except HTTPException:
        print('已有巡查运行，本次未重复启动')
        return 1
    print(encode({k: v for k, v in result.items() if k != 'namespace'}))
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    sys.exit(main())
