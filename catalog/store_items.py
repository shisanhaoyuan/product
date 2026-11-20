"""Manual management of offers returned by the bound US Marketplace account."""
import json
import re
from collections import OrderedDict
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit
from contextlib import contextmanager
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .credential_vault import decrypt, VaultError
from .database import write_lock
from .giga_presentation import number
from .walmart_accounts import now
from .walmart_client import WalmartError
from .walmart_publishing import binding, normalize_nodes


class Change(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    partner: str = Field(min_length=1, max_length=300)
    revision: int = Field(ge=1)
    sku: str = Field(min_length=1, max_length=200)
    kind: Literal['price', 'stock', 'retire']
    expected: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)
    node: str = Field(default='', max_length=100)
    confirm_sku: str = ''

    @model_validator(mode='after')
    def valid(self):
        if self.sku in ('.', '..') or any(ord(c) < 32 for c in self.sku):
            raise ValueError('无效 SKU')
        if self.kind == 'stock':
            if not self.node or not self.target.isascii() or not self.target.isdigit() or int(self.target) > 2147483647:
                raise ValueError('请选择仓库并填写非负整数库存')
        elif self.node:
            raise ValueError('此操作无需仓库')
        if self.kind == 'price':
            val = number(self.target)
            if val is None or not 0 < val <= 99999999 or val.as_tuple().exponent < -2:
                raise ValueError('售价须为有效 USD 金额，最多两位小数')
        if self.kind == 'retire' and (self.confirm_sku != self.sku or self.target != 'RETIRED'):
            raise ValueError('请确认永久下架的完整 SKU')
        return self


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS walmart_offer_changes(
        id TEXT PRIMARY KEY,store_id INTEGER NOT NULL REFERENCES listing_stores(id),
        partner TEXT NOT NULL,sku TEXT NOT NULL,kind TEXT NOT NULL,node TEXT NOT NULL,
        actor INTEGER NOT NULL,at TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,
        target TEXT NOT NULL,message TEXT NOT NULL)""")
    db.execute('CREATE INDEX IF NOT EXISTS offer_changes_store ON walmart_offer_changes(store_id,partner,at)')


def image_url(value):
    if not isinstance(value, str):
        return ''
    if re.fullmatch(r'/api/products/images/assets/[a-f0-9]{64}', value):
        return value
    try:
        url = urlsplit(value)
        return value if url.scheme in ('http', 'https') and url.hostname and not url.username and not url.password else ''
    except ValueError:
        return ''


def primary_image(row):
    for key in ('mainImageUrl', 'mainImageURL', 'primaryImageUrl', 'thumbnailUrl', 'imageUrl'):
        if url := image_url(row.get(key)):
            return url
    images = row.get('images')
    if isinstance(images, list):
        for image in images:
            if url := image_url(image.get('url') if isinstance(image, dict) else image):
                return url
    return ''


def normalized_gtin(value):
    value = str(value or '').strip()
    return value.zfill(14) if re.fullmatch(r'(?:[0-9]{8}|[0-9]{12,14})', value) and int(value) else ''


def catalog_image(raw, item):
    """Identifier-only search, never use a title match or another variant's image.

    Official DEFAULT response: items[].images[].url; it may omit GTIN.
    https://developer.walmart.com/us-marketplace/reference/getsearchresult
    """
    rows = raw.get('items')
    if not isinstance(rows, list):
        return ''
    matches = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        identifiers = [row[k] for k in ('gtin', 'upc', 'ean') if row.get(k)]
        if identifiers:
            if any(normalized_gtin(v) != normalized_gtin(item['gtin']) for v in identifiers):
                continue
        elif item.get('item_id'):
            if str(row.get('itemId') or '') != item['item_id']:
                continue
        elif len(rows) != 1:
            continue
        matches.append(row)
    return primary_image(matches[0]) if len(matches) == 1 else ''


def attach_images(db, items, store_id, partner):
    """Only decorate API-returned rows; never expand the list with local items."""
    missing = {i['sku']: i for i in items if not i['image']}
    if not missing:
        return
    marks = ','.join('?' for _ in missing)
    records = db.execute(f"""SELECT c.platform_sku,a.rows_json FROM walmart_listing_claims c
        JOIN walmart_listing_attempts a ON a.id=c.attempt_id AND a.store_id=c.store_id AND a.batch_id=c.batch_id
        WHERE c.store_id=? AND a.partner_id=? AND c.platform_sku IN ({marks})""",
        (store_id, partner, *missing))
    for record in records:
        row = missing[record['platform_sku']]
        matches = [r for r in json.loads(record['rows_json']) if r.get('platform_sku') == row['sku']]
        if len(matches) == 1:
            url = image_url((matches[0].get('visible') or {}).get('mainImageUrl'))
            if url:
                row.update(image=url, image_source='submitted')


def item_info(row):
    if not isinstance(row, dict) or not isinstance(row.get('sku'), str) or not row['sku']:
        raise WalmartError('沃尔玛返回的商品缺少 SKU')
    price = row.get('price') or {}
    value = number(price.get('amount')) if isinstance(price, dict) else None
    image = primary_image(row)
    return dict(image=image, image_source='walmart' if image else '', sku=row['sku'], name=row.get('productName') or '', gtin=row.get('gtin') or '',
                item_id=str(row.get('itemId') or ''),
                lifecycle=row.get('lifecycleStatus') or '', published=row.get('publishedStatus') or '',
                price=str(value) if value is not None else None,
                currency=price.get('currency') if isinstance(price, dict) else '')


def exact_item(remote, sku):
    rows = remote.item(sku).get('ItemResponse')
    if not isinstance(rows, list) or len(rows) != 1 or item_info(rows[0])['sku'] != sku:
        raise WalmartError('沃尔玛未返回完全匹配的 SKU，请刷新商品列表')
    return item_info(rows[0])


def stock_value(remote, sku, node):
    raw = remote.inventory(sku, node)
    quantity = raw.get('quantity') or {}
    value = quantity.get('amount') if isinstance(quantity, dict) else None
    if (raw.get('sku') != sku or raw.get('shipNode', node) != node or type(value) is not int
            or value < 0 or quantity.get('unit') != 'EACH'):
        raise WalmartError('该发货仓的库存未能核实，请刷新后重试')
    return str(value)


def observed(remote, item, kind, node):
    if kind == 'stock':
        return stock_value(remote, item['sku'], node)
    if kind == 'retire':
        return item['lifecycle']
    if item['currency'] != 'USD' or item['price'] is None:
        raise WalmartError('沃尔玛未返回可核对的美元售价')
    return item['price']


def equivalent(kind, a, b):
    if kind == 'retire':
        return a == b
    return number(a) is not None and number(a) == number(b)


def check_node(remote, node):
    if not any(n['id'] == node for n in normalize_nodes(remote.nodes())):
        raise HTTPException(409, '目标发货仓不属于当前店铺或已停用')


def finish(db, key, status, message):
    db.execute('UPDATE walmart_offer_changes SET status=?,message=? WHERE id=?', (status, message, key))
    return dict(status=status, message=message)


def store_item_routes(catalog):
    router = APIRouter(prefix='/api/store-items')
    image_cache = OrderedDict()
    image_lock = Lock()

    @contextmanager
    def session(request, sid):
        with catalog.connect() as db:
            _, bound = binding(db, sid)
        try:
            with request.app.state.walmart_publisher.session(
                    decrypt(sid, bound['encrypted_credentials']), bound['partner_id']) as remote:
                yield remote, bound
        except (WalmartError, VaultError) as exc:
            raise HTTPException(502, str(exc)) from None

    @router.get('')
    def stores():
        with catalog.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT s.id,s.name,a.partner_id FROM listing_stores s JOIN walmart_accounts a ON a.store_id=s.id "
                "WHERE s.active=1 AND a.status='verified' AND a.encrypted_credentials<>'' ORDER BY s.name")]

    @router.get('/{sid}')
    def fetch(sid: int, request: Request, cursor: str = Query('*', min_length=1, max_length=8192),
              sku: str = Query('', max_length=200)):
        with session(request, sid) as (remote, bound):
            raw = remote.items(cursor, sku)
            rows = raw.get('ItemResponse')
            if not isinstance(rows, list):
                raise WalmartError('沃尔玛商品列表响应异常')
            items = [item_info(row) for row in rows]
            if len({i['sku'] for i in items}) != len(items) or not isinstance(raw.get('nextCursor') or '', str):
                raise WalmartError('沃尔玛分页或 SKU 响应异常')
            if sku:
                items = [item for item in items if item['sku'] == sku]
            with catalog.connect() as db:
                attach_images(db, items, sid, bound['partner_id'])
            return dict(items=items, cursor=raw.get('nextCursor') or '', total=raw.get('totalItems'),
                        partner=bound['partner_id'], revision=bound['revision'])

    @router.get('/{sid}/nodes')
    def nodes(sid: int, request: Request):
        with session(request, sid) as (remote, _):
            return normalize_nodes(remote.nodes())

    @router.get('/{sid}/image')
    def fetch_image(sid: int, request: Request, sku: str = Query(min_length=1, max_length=200),
                    partner: str = Query(min_length=1, max_length=300), revision: int = Query(ge=1)):
        if sku in ('.', '..') or any(ord(c) < 32 for c in sku):
            raise HTTPException(422, '无效 SKU')
        with catalog.connect() as db:
            _, bound = binding(db, sid, partner)
        if bound['revision'] != revision:
            raise HTTPException(409, '店铺绑定已变化，请刷新商品列表')
        key = (sid, partner, revision, sku)
        with image_lock:
            cached = image_cache.get(key)
            if cached and cached[0] > monotonic():
                image_cache.move_to_end(key)
                return cached[1]
        result = dict(sku=sku, image='', image_source='', image_status='missing')
        try:
            with session(request, sid) as (remote, current):
                if current['partner_id'] != partner or current['revision'] != revision:
                    raise HTTPException(409, '店铺绑定已变化，请刷新商品列表')
                # Establish ownership and GTIN from this account, never trust a browser-supplied GTIN.
                item = exact_item(remote, sku)
                with catalog.connect() as db:
                    attach_images(db, [item], sid, partner)
                gtin = normalized_gtin(item['gtin'])
                if not item['image'] and gtin:
                    item['image'] = catalog_image(remote.catalog_search(gtin), item)
                    item['image_source'] = 'walmart' if item['image'] else ''
                result.update(image=item['image'], image_source=item['image_source'],
                              image_status='ready' if item['image'] else 'missing')
        except HTTPException as exc:
            if exc.status_code != 502:
                raise
            result['image_status'] = 'unavailable'
        with catalog.connect() as db:
            _, current = binding(db, sid, partner)
            if current['revision'] != revision:
                raise HTTPException(409, '店铺绑定已变化，请刷新商品列表')
        with image_lock:
            image_cache[key] = (monotonic() + (1800 if result['image'] else 60), result)
            image_cache.move_to_end(key)
            while len(image_cache) > 1000:
                image_cache.popitem(last=False)
        return result

    @router.get('/{sid}/stock')
    def stock(sid: int, request: Request, sku: str = Query(min_length=1,max_length=200),
              node: str = Query(min_length=1,max_length=100)):
        with session(request, sid) as (remote, _):
            check_node(remote, node)
            return dict(quantity=stock_value(remote, sku, node))

    @router.get('/{sid}/history')
    def history(sid: int):
        with catalog.connect() as db:
            _, bound = binding(db, sid)
            return [dict(row) for row in db.execute("""SELECT id,sku,kind,node,at,status,target,message
                FROM walmart_offer_changes WHERE store_id=? AND partner=? ORDER BY at DESC LIMIT 50""",
                (sid, bound['partner_id']))]

    @router.post('/{sid}/changes/{key}/check')
    def recheck(sid: int, key: UUID, request: Request):
        with session(request, sid) as (remote, bound), catalog.connect() as db:
            write_lock(db)
            _, current = binding(db, sid, bound['partner_id'])
            if current['revision'] != bound['revision']:
                raise HTTPException(409, '店铺绑定已变化，请刷新后核对')
            row = db.execute('SELECT * FROM walmart_offer_changes WHERE id=? AND store_id=? AND partner=?',
                             (str(key), sid, bound['partner_id'])).fetchone()
            if not row:
                raise HTTPException(404, '操作记录不存在')
            if row['status'] == 'pending':
                value = observed(remote, exact_item(remote, row['sku']), row['kind'], row['node'])
                if equivalent(row['kind'], value, row['target']):
                    return finish(db, str(key), 'verified', '沃尔玛回读已确认')
            return dict(status=row['status'], message=row['message'])

    @router.post('/{sid}/changes')
    def change(sid: int, data: Change, request: Request):
        key, payload = str(data.request_id), data.model_dump_json()
        actor = getattr(request.state, 'user', {}).get('id', 0)
        with session(request, sid) as (remote, bound):
            if data.partner != bound['partner_id'] or data.revision != bound['revision']:
                raise HTTPException(409, '店铺绑定有变化，请重新加载商品')
            with catalog.connect() as db:
                write_lock(db)
                prior = db.execute('SELECT * FROM walmart_offer_changes WHERE id=?', (key,)).fetchone()
                if prior:
                    if prior['store_id'] != sid or prior['payload'] != payload:
                        raise HTTPException(409, '操作编号已用于其他请求')
                    return dict(status=prior['status'], message=prior['message'])
                automatic = db.execute("SELECT 1 FROM giga_patrol_actions WHERE store_id=? AND platform_sku=? "
                                       "AND status IN ('pending','sending','queued')", (sid, data.sku)).fetchone()
                if automatic:
                    raise HTTPException(409, '巡查更新正在执行或待确认，请先核对巡查明细')
                # Record intent before sending so a disconnected browser cannot replay the write.
                pending = db.execute("""SELECT 1 FROM walmart_offer_changes WHERE store_id=? AND partner=?
                    AND sku=? AND status='pending' AND (kind='retire' OR ?='retire' OR (kind=? AND node=?))""",
                    (sid, data.partner, data.sku, data.kind, data.kind, data.node)).fetchone()
                if pending:
                    raise HTTPException(409, '已有待确认的修改，请先在操作记录中核对')
                db.execute('INSERT INTO walmart_offer_changes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (key, sid, data.partner, data.sku, data.kind, data.node, actor, now(), payload,
                            'pending', data.target, '等待沃尔玛结果，请回读核对'))
            sent = False
            try:
                remote.require_scope({'price':'price','stock':'inventory','retire':'item'}[data.kind])
                if data.kind == 'stock':
                    check_node(remote, data.node)
                with catalog.connect() as db:
                    write_lock(db)
                    _, current = binding(db, sid, data.partner)
                    if current['revision'] != data.revision:
                        raise HTTPException(409, '绑定发生变化，请刷新页面')
                    item = exact_item(remote, data.sku)
                    current_value = observed(remote, item, data.kind, data.node)
                    if equivalent(data.kind, current_value, data.target):
                        return finish(db, key, 'verified', '沃尔玛当前值已达到目标')
                    if item['lifecycle'] == 'RETIRED' or not equivalent(data.kind, current_value, data.expected):
                        raise HTTPException(409, '商品状态或原值已变化，请刷新后重新确认')
                    sent = True
                    if data.kind == 'price':
                        remote.set_price(data.sku, Decimal(data.target))
                    elif data.kind == 'stock':
                        remote.set_inventory(data.sku, int(data.target), data.node)
                    else:
                        ack = remote.retire(data.sku)
                        if ack.get('sku') != data.sku:
                            raise WalmartError('下架回执 SKU 不匹配')
                value = observed(remote, exact_item(remote, data.sku), data.kind, data.node)
                with catalog.connect() as db:
                    if equivalent(data.kind, value, data.target):
                        return finish(db, key, 'verified', '沃尔玛回读已确认')
                    return finish(db, key, 'pending', '已提交，沃尔玛尚未显示目标值')
            except Exception as exc:
                message = (str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                           if isinstance(exc, WalmartError) else '操作异常，请刷新并核对沃尔玛结果')
                with catalog.connect() as db:
                    if sent:
                        return finish(db, key, 'pending', '请求已发送，结果待核对。' + message)
                    finish(db, key, 'failed', message)
                if isinstance(exc, (HTTPException, WalmartError)):
                    raise
                raise HTTPException(502, message) from None
    return router
