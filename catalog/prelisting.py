"""Reviewable listing plans. No publisher or external side effects live here."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .blacklist import active_entries, matches, seller_key
from .database import INTEGRITY_ERRORS, write_lock, read_snapshot, insert_id, is_postgres

# Only names visible in the user's screenshots; marketplace aliases are explicit.
SCREENSHOT_STORES = [
    ('MY-H-20', 'MY-H-20'), ('MY-HDS', 'MY-HDS'), ('MY-L-16', 'HomeYao'),
    ('MY-W-1', 'MY-W-1'), ('MY-Z-6', 'MY-Z-6'), ('MY-Z-7', 'MY-Z-7'),
    *[(f'MYZ-L-{n}', f'MYZ-L-{n}') for n in [1, 2, 10, 104, 109, 11, 12, 13, 15, 17, 18, 19, 20]],
    ('MYZ-L-14', 'YQ1'),
]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def initialize(db):
    if not is_postgres(db):
        db.executescript('''
        CREATE TABLE IF NOT EXISTS listing_stores(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
            marketplace_name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            origin TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS prelisting_batches(
            id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            actor_id INTEGER, settings_json TEXT NOT NULL, preview_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS prelisting_items(
            batch_id TEXT NOT NULL REFERENCES prelisting_batches(id),
            listing_id TEXT NOT NULL REFERENCES listings(id), source_digest TEXT NOT NULL,
            source_json TEXT NOT NULL, PRIMARY KEY(batch_id,listing_id));
        CREATE TABLE IF NOT EXISTS prelisting_claims(
            source_key TEXT NOT NULL, asin TEXT NOT NULL,
            store_id INTEGER NOT NULL REFERENCES listing_stores(id),
            batch_id TEXT NOT NULL REFERENCES prelisting_batches(id),
            UNIQUE(source_key,asin,store_id));
        CREATE TABLE IF NOT EXISTS prelisting_events(
            id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES prelisting_batches(id),
            at TEXT NOT NULL, actor_id INTEGER, action TEXT NOT NULL);
    ''')
    if not db.execute('SELECT 1 FROM migrations WHERE version=4').fetchone():
        stores = [(name, alias, 'user_screenshot') for name, alias in SCREENSHOT_STORES]
        stores += [(r[0], r[0], 'catalog') for r in db.execute('SELECT DISTINCT store FROM listings') if r[0].strip()]
        db.executemany('INSERT INTO listing_stores(name,name_key,marketplace_name,origin) VALUES(?,?,?,?) ON CONFLICT DO NOTHING',
                       [(name, seller_key(name), alias, origin) for name, alias, origin in stores])
        db.execute('INSERT INTO migrations VALUES(4)')


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class StoreInput(StrictInput):
    name: str = Field(min_length=1, max_length=100)
    marketplace_name: str = Field(default='', max_length=100)

    @field_validator('name', 'marketplace_name')
    @classmethod
    def clean(cls, value, info):
        value = value.strip()
        if info.field_name == 'name' and not value:
            raise ValueError('店铺名称不能为空')
        return value


class StoreState(StrictInput):
    active: bool


class Filters(StrictInput):
    deduplicate: bool = False
    q: str = Field(default='', max_length=300)
    store: str = Field(default='', max_length=100)
    brand: str = Field(default='', max_length=100)
    status: Literal['', 'complete', 'review', 'saved', 'unconfirmed', 'reopened', 'snapshot'] = ''
    date: str = Field(default='', max_length=32)
    category: str = Field(default='', max_length=300)
    seller_status: Literal['', 'blocked', 'unblocked'] = ''


class PlanInput(StrictInput):
    listing_ids: list[str] | None = Field(default=None, min_length=1, max_length=500)
    variant_skus: dict[str, list[str]] | None = None
    filters: Filters | None = None
    store_id: int = Field(gt=0)
    brand: str = Field(min_length=1, max_length=100)
    multiplier: Decimal = Field(gt=0, le=100, max_digits=8, decimal_places=4)

    @field_validator('brand')
    @classmethod
    def brand_name(cls, value):
        if not value.strip():
            raise ValueError('品牌不能为空')
        return value.strip()

    @model_validator(mode='after')
    def selection(self):
        if (self.listing_ids is None) == (self.filters is None):
            raise ValueError('请选择商品 ID 列表或筛选条件之一')
        if self.listing_ids is not None and len(set(self.listing_ids)) != len(self.listing_ids):
            raise ValueError('不能重复选择同一商品')
        if self.variant_skus is not None:
            if self.listing_ids is None or set(self.variant_skus) != set(self.listing_ids):
                raise ValueError('子 SKU 选择必须逐个对应明确的商品 ID')
            for skus in self.variant_skus.values():
                if not 1 <= len(skus) <= 5000 or len(set(skus)) != len(skus) or any(not s or len(s) > 200 for s in skus):
                    raise ValueError('请选择不重复的有效子 SKU')
        return self


class CreateInput(PlanInput):
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    request_key: uuid.UUID


class BatchAction(StrictInput):
    action: Literal['confirm', 'cancel']


def money(value, *, zero=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('缺少有效金额')
    if not result.is_finite() or result < 0 or (not zero and result == 0) or result > Decimal('10000000'):
        raise ValueError('金额超出有效范围')
    return result


def planned_price(row, check, multiplier, variant):
    """Use an explicit SKU audit, never infer a base by dividing a marked-up price."""
    try:
        if not row.get('asin'):
            raise ValueError('缺少明确的 SKU→ASIN 对应关系')
        if check.get('status') != 'verified':
            raise ValueError('缺少该 SKU 已核验的价格依据')
        if variant and check.get('sku') != row['sku']:
            raise ValueError('价格审计与子 SKU 不一致')
        facts = row.get('source_facts', {})
        frozen = facts.get('frozen_price_basis', {})
        reference = check.get('price_reference', {})
        if not isinstance(frozen, dict) or not isinstance(reference, dict):
            raise ValueError('价格依据格式异常，需要重新核验')
        if frozen.get('source') == 'missing_verified_offer' or facts.get('offer_ambiguous'):
            raise ValueError('来源报价异常，需要重新核验')
        if facts.get('currency') not in (None, '', 'USD'):
            raise ValueError('来源报价不是 USD')
        refs = [facts.get('asin'), frozen.get('source_asin'), reference.get('asin')]
        if any(a and a != row['asin'] for a in refs):
            raise ValueError('来源 ASIN 与价格证据不一致')
        landed = money(check.get('landed_price'))
        original = money(check.get('existing_price'), zero=True)
        base = max(landed, original)
        if money(check.get('selected_basis')) != base:
            raise ValueError('历史价格基准与取较高值规则不一致')
        if money(check.get('actual')) != money(row.get('price')):
            raise ValueError('当前记录价格与核验结果不一致')
        if frozen.get('source_price') is not None:
            if money(frozen.get('source_price')) + money(frozen.get('shipping_price'), zero=True) != landed:
                raise ValueError('报价与运费合计不一致')
        # Preserve the existing floor-to-dollars + .99 variant rule.
        final = base * multiplier
        final = (final.to_integral_value(rounding=ROUND_FLOOR) + Decimal('.99') if variant
                 else final.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
        return dict(base=f'{base:.2f}', landed=f'{landed:.2f}', original=f'{original:.2f}',
                    price=f'{final:.2f}', error='')
    except (ValueError, InvalidOperation) as exc:
        return dict(base=None, landed=None, original=None, price=None, error=str(exc))


def build_preview(catalog, db, data, exclude_batch=None):
    store = db.execute('SELECT * FROM listing_stores WHERE id=? AND active=1', (data.store_id,)).fetchone()
    if not store:
        raise HTTPException(409, '目标店铺不存在或已停用')
    if data.listing_ids is not None:
        placeholders = ','.join('?' for _ in data.listing_ids)
        rows = db.execute(f'SELECT * FROM current_listings AS listings WHERE id IN ({placeholders}) ORDER BY uploaded_at DESC,id', data.listing_ids).fetchall()
        if len(rows) != len(data.listing_ids):
            raise HTTPException(409, '部分所选商品不存在，请刷新商品库')
    else:
        query, args = catalog.selection_sql(**data.filters.model_dump())
        rows = db.execute(f'SELECT * FROM ({query}) AS listings ORDER BY uploaded_at DESC,id LIMIT 501', args).fetchall()
    if not rows or len(rows) > 500:
        raise HTTPException(422, '每批请选择 1–500 个商品；请缩小筛选范围')
    entries = active_entries(db)
    items, seen, conflict = [], set(), False
    for row in rows:
        d = json.loads(row['data_json'])
        key = (seller_key(d['source']), d['asin'])
        errors, warnings = [], ['使用历史核验记录；正式执行前需重新采集价格和库存']
        if d.get('local_edit', {}).get('changed'):
            errors.append('存在本地手工修改，尚未同步 ERP 或重新核验')
        reserved = db.execute('SELECT batch_id FROM prelisting_claims WHERE source_key=? AND asin=? AND store_id=?', (*key, data.store_id)).fetchone()
        if key in seen or (reserved and reserved['batch_id'] != exclude_batch):
            errors.append('同来源 ASIN 已在本次选择或该目标店铺的未取消批次中'); conflict = True
        seen.add(key)
        blocked = matches(d, entries)
        errors += [f"卖家已拉黑：{b['seller']}（{b['reason']}）" for b in blocked]
        if not d['fill_complete']:
            errors.append('商品填写尚未核验完整')
        children = d['variants']
        if data.variant_skus is not None:
            selected = set(data.variant_skus[row['id']])
            if not selected.issubset({v['sku'] for v in children}):
                raise HTTPException(409, '所选子 SKU 不属于该商品或已不存在，请刷新后重新选择')
            children = [v for v in children if v['sku'] in selected]
        if children:
            priced = [dict(sku=v['sku'], asin=v['asin'], current_price=v['price'], stock=v['stock'],
                           content={k: v.get(k) for k in ('images', 'attributes', 'product_id_type', 'ean',
                               'swatch_attribute', 'swatch_image', 'shipping_weight', 'copy', 'source_url', 'seller')},
                           **planned_price(v, v['price_check'], data.multiplier, True)) for v in children]
        else:
            raw_checks = d['checks'].get('pricing_checks', [])
            checks = [c for c in raw_checks if isinstance(c, dict) and c.get('field') == 'dc_price'] if isinstance(raw_checks, list) else []
            priced = [dict(sku=d['sku'], asin=d['asin'], current_price=d['price'], stock=d['stock'],
                           **planned_price(d, checks[0] if len(checks) == 1 else {}, data.multiplier, False))]
        errors += [f"{v['sku']}：{v['error']}" for v in priced if v['error']]
        errors += [f"{v['sku']}：库存缺失或不符合 0 / 10 规则，执行前需核验" for v in priced if str(v['stock']) not in {'0', '10'}]
        if not d['seller'] or any(not v['seller'] for v in children):
            warnings.append('部分卖家未采集，无法完整检查卖家黑名单')
        msrp = None if any(v['price'] is None for v in priced) else f"{max(Decimal(v['price']) for v in priced)*2:.2f}"
        items.append(dict(listing_id=row['id'], source_digest=row['digest'], source=d['source'],
                          asin=d['asin'], sku=d['sku'], title=d['copy']['title'], source_store=d['store'],
                          template=d['template'], observed_at=d['observed_at'], variants=priced, msrp=msrp,
                          status='review' if errors else 'ready', errors=errors, warnings=warnings))
    result = dict(store=dict(store), brand=data.brand, multiplier=str(data.multiplier), items=items,
                  total=len(items), variant_count=sum(len(d['variants']) for d in items),
                  ready_count=sum(d['status'] == 'ready' for d in items), can_create=not conflict,
                  execution_enabled=False, price_rule='verified_higher_basis_v1')
    result['preview_hash'] = fingerprint(result)
    return result, rows


def prelisting_routes(catalog):
    router = APIRouter()

    def actor(request):
        return getattr(request.state, 'user', {}).get('id')

    def event(db, bid, request, action, at):
        db.execute('INSERT INTO prelisting_events(batch_id,at,actor_id,action) VALUES(?,?,?,?)',
                   (bid, at, actor(request), action))

    def batch(db, bid):
        row = db.execute('SELECT * FROM prelisting_batches WHERE id=?', (bid,)).fetchone()
        if not row:
            raise HTTPException(404, '批次不存在')
        return dict(id=row['id'], status=row['status'], created_at=row['created_at'], updated_at=row['updated_at'],
                    preview=json.loads(row['preview_json']), execution_enabled=False)

    @router.get('/api/listing-stores')
    def stores():
        with catalog.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM listing_stores ORDER BY lower(name)')]

    @router.post('/api/listing-stores', status_code=201)
    def add_store(data: StoreInput):
        try:
            with catalog.connect() as db:
                write_lock(db)
                store_id = insert_id(db, "INSERT INTO listing_stores(name,name_key,marketplace_name,origin) VALUES(?,?,?,'manual')",
                                 (data.name, seller_key(data.name), data.marketplace_name or data.name))
                return dict(db.execute('SELECT * FROM listing_stores WHERE id=?', (store_id,)).fetchone())
        except INTEGRITY_ERRORS:
            raise HTTPException(409, '店铺已经存在，请使用已有店铺')

    @router.patch('/api/listing-stores/{store_id}')
    def store_status(store_id: int, data: StoreState):
        with catalog.connect() as db:
            write_lock(db)
            if db.execute('UPDATE listing_stores SET active=? WHERE id=?', (int(data.active), store_id)).rowcount != 1:
                raise HTTPException(404, '店铺不存在')
            return {'ok': True}

    @router.post('/api/prelisting/preview')
    def preview(data: PlanInput):
        with catalog.connect() as db:
            read_snapshot(db)
            return build_preview(catalog, db, data)[0]

    @router.post('/api/prelisting/batches', status_code=201)
    def create(data: CreateInput, request: Request):
        request_hash = fingerprint(data.model_dump(mode='json'))
        with catalog.connect() as db:
            write_lock(db)
            prior = db.execute('SELECT id,request_hash FROM prelisting_batches WHERE request_key=?', (str(data.request_key),)).fetchone()
            if prior:
                if prior['request_hash'] != request_hash:
                    raise HTTPException(409, '重复请求的参数发生变化，请重新预览')
                return batch(db, prior['id'])
            result, rows = build_preview(catalog, db, data)
            if result['preview_hash'] != data.preview_hash:
                raise HTTPException(409, '商品、名单或店铺状态已变化，请重新预览')
            if not result['can_create']:
                raise HTTPException(409, '包含重复或已在未取消批次中的商品，请调整选择')
            bid, now = uuid.uuid4().hex, datetime.now(timezone.utc).isoformat()
            settings = dict(listing_ids=[r['id'] for r in rows], store_id=data.store_id, brand=data.brand, multiplier=str(data.multiplier))
            if data.variant_skus is not None:
                settings['variant_skus'] = data.variant_skus
            db.execute('INSERT INTO prelisting_batches VALUES(?,?,?,?,?,?,?,?,?)',
                       (bid, str(data.request_key), request_hash, 'draft', now, now, actor(request), encoded(settings), encoded(result)))
            for row, item in zip(rows, result['items']):
                db.execute('INSERT INTO prelisting_items VALUES(?,?,?,?)', (bid,row['id'],row['digest'],row['data_json']))
                db.execute('INSERT INTO prelisting_claims VALUES(?,?,?,?)', (seller_key(item['source']),item['asin'],data.store_id,bid))
            event(db, bid, request, 'create', now)
            return batch(db, bid)

    @router.get('/api/prelisting/batches')
    def batches(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        with catalog.connect() as db:
            rows = db.execute('SELECT * FROM prelisting_batches ORDER BY created_at DESC,id LIMIT ? OFFSET ?', (page_size,(page-1)*page_size)).fetchall()
            items=[]
            for r in rows:
                p=json.loads(r['preview_json'])
                items.append(dict(id=r['id'],status=r['status'],created_at=r['created_at'],store=p['store']['name'],
                                  brand=p['brand'],multiplier=p['multiplier'],total=p['total'],ready_count=p['ready_count'],variant_count=p['variant_count']))
            return dict(items=items,total=db.execute('SELECT COUNT(*) FROM prelisting_batches').fetchone()[0],page=page,page_size=page_size)

    @router.get('/api/prelisting/batches/{bid}')
    def detail(bid: str):
        with catalog.connect() as db:
            return batch(db, bid)

    @router.post('/api/prelisting/batches/{bid}/action')
    def action(bid: str, data: BatchAction, request: Request):
        with catalog.connect() as db:
            write_lock(db)
            current=batch(db,bid)
            target='confirmed' if data.action == 'confirm' else 'cancelled'
            if current['status'] == target:
                return current
            if current['status'] == 'cancelled':
                raise HTTPException(409, '已取消的批次不能确认，请重新创建')
            if target == 'confirmed':
                row=db.execute('SELECT settings_json FROM prelisting_batches WHERE id=?',(bid,)).fetchone()
                refreshed,_=build_preview(catalog,db,PlanInput.model_validate_json(row[0]),exclude_batch=bid)
                if refreshed['preview_hash'] != current['preview']['preview_hash']:
                    raise HTTPException(409,'商品或规则状态已变化，请取消此批次并重新预览创建')
                if refreshed['ready_count'] != refreshed['total']:
                    raise HTTPException(409,'批次存在待复核项，请补齐来源数据后重新创建')
            now=datetime.now(timezone.utc).isoformat()
            db.execute('UPDATE prelisting_batches SET status=?,updated_at=? WHERE id=?',(target,now,bid))
            if target == 'cancelled':
                db.execute('DELETE FROM prelisting_claims WHERE batch_id=?',(bid,))
            event(db,bid,request,data.action,now)
            return batch(db,bid)

    return router
