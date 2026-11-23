"""Reviewable listing plans. No publisher or external side effects live here."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, BackgroundTasks
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .blacklist import active_entries, matches, seller_key
from .database import INTEGRITY_ERRORS, write_lock, read_snapshot, insert_id, is_postgres
from . import platform_sku
from .product_search import normalize_identifiers
from .product_ranges import ProductRanges
from .giga_prelisting import claim_identity, prepare as prepare_giga

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
    platform_sku.initialize(db)
    db.execute('''CREATE TABLE IF NOT EXISTS prelisting_batch_trash(
        batch_id TEXT PRIMARY KEY REFERENCES prelisting_batches(id),deleted_at TEXT NOT NULL,actor_id INTEGER)''')
    db.execute('INSERT INTO migrations VALUES(20) ON CONFLICT DO NOTHING')


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class StoreCodeInput(StrictInput):
    sku_code: str | None = Field(default=None, pattern=r'^[A-Z0-9]{2}$')

    @field_validator('sku_code', mode='before')
    @classmethod
    def normalize_code(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


class StoreInput(StoreCodeInput):
    name: str = Field(min_length=1, max_length=100)
    marketplace_name: str = Field(default='', max_length=100)

    @field_validator('name', 'marketplace_name')
    @classmethod
    def clean(cls, value, info):
        value = value.strip()
        if info.field_name == 'name' and not value:
            raise ValueError('店铺名称不能为空')
        return value


class StoreState(StoreCodeInput):
    active: bool | None = None

    @model_validator(mode='after')
    def changes(self):
        if not self.model_fields_set or ('active' in self.model_fields_set and self.active is None):
            raise ValueError('请提供店铺状态或两位 SKU 编码')
        return self


class Filters(StrictInput, ProductRanges):
    source: Literal['', 'Amazon', 'GigaB2B', 'GigaB2B_CA', 'Saleyee'] = ''
    deduplicate: bool = False
    q: str = Field(default='', max_length=300)
    identifiers: str = Field(default='', max_length=6000)
    inventory_status: Literal['', 'out_of_stock', 'in_stock'] = ''
    listed_store: str = Field(default='', pattern='^([0-9]+)?$')
    price_change: Literal['', 'up', 'down', 'unchanged', 'unknown'] = ''
    shipping_address: str = Field(default='', pattern='^([a-f0-9]{64})?$')
    return_address: str = Field(default='', pattern='^([a-f0-9]{64})?$')
    multi: str = Field(default='', max_length=30000)

    @field_validator('multi')
    @classmethod
    def valid_multi(cls, value):
        from .product_filters import normalize_multi
        return normalize_multi(value)

    store: str = Field(default='', max_length=100)
    brand: str = Field(default='', max_length=100)
    status: Literal['', 'complete', 'review', 'saved', 'unconfirmed', 'reopened', 'snapshot'] = ''
    date: str = Field(default='', max_length=32)
    category: str = Field(default='', max_length=300)
    seller_status: Literal['', 'blocked', 'unblocked'] = ''
    listing_count_min: int | None = Field(default=None, ge=1, le=2147483647)
    listing_count_max: int | None = Field(default=None, ge=1, le=2147483647)

    _identifiers = field_validator('identifiers')(normalize_identifiers)

    @model_validator(mode='after')
    def listing_count_range(self):
        if self.listing_count_min is not None and self.listing_count_max is not None and self.listing_count_min > self.listing_count_max:
            raise ValueError('上架次数下限不能大于上限')
        return self


class PlanInput(StrictInput):
    listing_ids: list[str] | None = Field(default=None, min_length=1, max_length=500)
    variant_skus: dict[str, list[str]] | None = None
    filters: Filters | None = None
    store_id: int = Field(gt=0)
    brand: str = Field(min_length=1, max_length=100)
    multiplier: Decimal = Field(gt=0, le=100, max_digits=8, decimal_places=4)
    template_batches: dict[str, str] = Field(default_factory=dict, max_length=500)

    @field_validator('brand')
    @classmethod
    def brand_name(cls, value):
        if not value.strip():
            raise ValueError('品牌不能为空')
        return value.strip()

    @model_validator(mode='after')
    def selection(self):
        if any(not k or len(k)>100 or not v or len(v)>100 for k,v in self.template_batches.items()):
            raise ValueError('请选择有效的商品模板')
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
    action: Literal['confirm', 'cancel', 'upgrade_skus']


class BatchBulkAction(StrictInput):
    ids: list[str] = Field(min_length=1,max_length=100)
    action: Literal['delete','restore']

    @field_validator('ids')
    @classmethod
    def unique_ids(cls, values):
        if len(set(values))!=len(values) or any(not v or len(v)>100 for v in values):
            raise ValueError('请选择不重复的有效批次')
        return values


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


def selected_rows(catalog, db, data):
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
    if data.variant_skus is not None:
        for row in rows:
            children=json.loads(row['data_json'])['variants']
            if not set(data.variant_skus[row['id']]).issubset({v['sku'] for v in children}):
                raise HTTPException(409, '所选子 SKU 不属于该商品或已不存在，请刷新后重新选择')
    return rows


def variant_images(product, variant):
    """Use current images for this ASIN; explicit child edits take precedence."""
    images = variant.get('images') or []
    changes = (product.get('local_edit') or {}).get('changes') or []
    child_edited = any(c.get('field') == 'images' and c.get('variant_sku') == variant['sku'] for c in changes)
    parent_edited = any(c.get('field') == 'images' and not c.get('variant_sku') for c in changes)
    if (product.get('source') == 'Amazon' and variant.get('asin')
            and product.get('asin') == variant['asin'] and not child_edited
            and (parent_edited or not images)):
        images = product.get('images') or []
    return list(dict.fromkeys(images))


def build_preview(catalog, db, data, exclude_batch=None):
    from .listing_prerequisites import pool_lookup
    from .amazon_pricing import price_plan as amazon_price
    has_pool = bool(db.execute('SELECT 1 FROM listing_real_identifiers LIMIT 1').fetchone())
    store = db.execute('SELECT * FROM listing_stores WHERE id=? AND active=1', (data.store_id,)).fetchone()
    if not store:
        raise HTTPException(409, '目标店铺不存在或已停用')
    bound = db.execute('SELECT marketplace,encrypted_credentials FROM walmart_accounts WHERE store_id=?', (data.store_id,)).fetchone()
    if bound and bound['encrypted_credentials'] and bound['marketplace'] != 'US':
        raise HTTPException(409, '该店铺绑定的是加拿大站；加拿大刊登尚未接入，暂不能创建预上架批次')
    if not store['sku_code']:
        raise HTTPException(409, '请先在店铺管理中设置该店铺唯一的两位 SKU 编码')
    rows = selected_rows(catalog, db, data)
    entries = active_entries(db)
    items, seen, seen_variants, conflict = [], set(), set(), False
    for row in rows:
        d = json.loads(row['data_json'])
        if d['source'] == 'GigaB2B_CA':
            raise HTTPException(409, '加拿大云仓商品需使用 Walmart 加拿大站店铺；加拿大站刊登接入后开放预上架，不能提交到美国站')
        is_supplier = d['source'] in ('GigaB2B', 'GigaB2B_CA', 'Saleyee')
        key = (seller_key(d['source']), claim_identity(d))
        errors, warnings = [], ['使用历史核验记录；正式执行前需重新采集价格和库存']
        edits = d.get('local_edit', {})
        capture_only = bool(edits.get('changes')) and not edits.get('conflicts') and not edits.get('source_changed') and all(
            c.get('capture_asin') for c in edits['changes'])
        if not is_supplier and edits.get('changed') and not capture_only:
            errors.append('存在本地手工修改，尚未同步 ERP 或重新核验')
        reserved = db.execute('SELECT batch_id FROM prelisting_claims WHERE source_key=? AND asin=? AND store_id=?', (*key, data.store_id)).fetchone()
        if key in seen or (reserved and reserved['batch_id'] != exclude_batch):
            errors.append(('同来源供应商 SKU' if is_supplier else '同来源 ASIN') + ' 已在本次选择或该目标店铺的未取消批次中'); conflict = True
        seen.add(key)
        blocked = matches(d, entries)
        errors += [f"卖家已拉黑：{b['seller']}（{b['reason']}）" for b in blocked]
        if not is_supplier and not (d['fill_complete'] or capture_only and edits.get('source_status', {}).get('fill_complete')):
            errors.append('商品填写尚未核验完整')
        children = d['variants']
        if data.variant_skus is not None:
            selected = set(data.variant_skus[row['id']])
            if not selected.issubset({v['sku'] for v in children}):
                raise HTTPException(409, '所选子 SKU 不属于该商品或已不存在，请刷新后重新选择')
            children = [v for v in children if v['sku'] in selected]
        if is_supplier:
            original = json.loads(db.execute('SELECT data_json FROM listings WHERE id=?', (row['id'],)).fetchone()[0])
            if d['source'] == 'Saleyee':
                from .saleyee_prelisting import prepare as prepare_supplier
            else:
                prepare_supplier = prepare_giga
            priced, supplier_errors, warnings = prepare_supplier(d, original, data.multiplier)
            errors.extend(supplier_errors)
        elif children:
            priced = [dict(sku=v['sku'], asin=v['asin'], current_price=v['price'], stock=v['stock'],
                           content={**{k: v.get(k) for k in ('attributes', 'product_id_type', 'ean',
                               'swatch_attribute', 'swatch_image', 'shipping_weight', 'copy', 'source_url', 'seller')},
                               'images': variant_images(d, v)},
                           **(amazon_price(db, row['id'], d, v, data.multiplier) if d['source'] == 'Amazon'
                              else planned_price(v, v['price_check'], data.multiplier, True))) for v in children]
        else:
            raw_checks = d['checks'].get('pricing_checks', [])
            checks = [c for c in raw_checks if isinstance(c, dict) and c.get('field') == 'dc_price'] if isinstance(raw_checks, list) else []
            priced = [dict(sku=d['sku'], asin=d['asin'], current_price=d['price'], stock=d['stock'],
                           **(amazon_price(db, row['id'], d, d, data.multiplier) if d['source'] == 'Amazon'
                              else planned_price(d, checks[0] if len(checks) == 1 else {}, data.multiplier, False)))]
        identities = [platform_sku.identity(d['source'], v) for v in priced]
        if len(set(identities)) != len(identities) or seen_variants.intersection(identities):
            errors.append('多个子 SKU 对应同一商品身份，无法分配唯一平台 SKU')
            conflict = True
        seen_variants.update(identities)
        for variant in priced:
            variant['platform_sku'] = platform_sku.lookup(db, data.store_id, d['source'], variant)
            assigned = pool_lookup(db,d['source'],variant['sku'],data.brand)
            if assigned:
                variant['assigned_identifier'] = dict(state='assigned',type=assigned['kind'],value=assigned['value'])
            elif has_pool:
                variant['assigned_identifier'] = dict(state='pending',type='EAN',value='')
        if not is_supplier:
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
                  execution_enabled=False,
                  price_rule=('giga_regular_landed_v1' if all(i['source'] in ('GigaB2B', 'GigaB2B_CA') for i in items)
                              else 'source_specific_v1' if any(i['source'] in ('GigaB2B', 'GigaB2B_CA', 'Saleyee') for i in items)
                              else 'amazon_captured_original_v1' if all(i['source'] == 'Amazon' for i in items)
                              else 'source_specific_v1'), sku_rule=platform_sku.RULE)
    from .listing_templates import annotate_preview
    annotate_preview(db, data, result, exclude_batch)
    # Allocation occurs only on creation; its persisted value is verified separately on confirmation.
    result['preview_hash'] = preview_fingerprint(result)
    return result, rows


def preview_fingerprint(result):
    hash_input = {k: v for k, v in result.items() if k != 'preview_hash'} | {'items': [item | {'variants': [
        {k: value for k, value in v.items() if k not in ('platform_sku','assigned_identifier')} for v in item['variants']
    ]} for item in result['items']]}
    return fingerprint(hash_input)


def upgrade_draft_skus(db, current):
    if current['status'] != 'draft':
        raise HTTPException(409, '只有未确认批次可以升级 SKU；历史确认或取消记录保持原样')
    preview = current['preview']
    if preview.get('sku_rule') not in (platform_sku.LEGACY_RULE, platform_sku.RULE):
        raise HTTPException(409, '旧批次没有 SKU 分配记录，请取消后重新创建')
    store = db.execute('SELECT * FROM listing_stores WHERE id=?', (preview['store']['id'],)).fetchone()
    if not store or not store['sku_code'] or store['sku_code'] != preview['store'].get('sku_code'):
        raise HTTPException(409, '店铺编码已变化，请重新创建批次')
    targets = {}
    for item in preview['items']:
        for variant in item['variants']:
            sku = platform_sku.lookup(db, store['id'], item['source'], variant)
            if not sku or sku != variant.get('platform_sku'):
                raise HTTPException(409, '批次 SKU 与分配记录不一致，未进行升级')
            if sku[4:].isascii() and sku[4:].isdigit():
                targets[platform_sku.identity(item['source'], variant)] = (item['source'], variant)
    if not targets and preview.get('sku_rule') == platform_sku.RULE:
        return False
    # Shared reservations cannot be changed underneath another active batch.
    others = db.execute("SELECT preview_json FROM prelisting_batches WHERE id<>? AND status<>'cancelled'", (current['id'],)).fetchall()
    for row in others:
        other = json.loads(row['preview_json'])
        if other['store']['id'] == store['id'] and any(
            platform_sku.identity(item['source'], variant) in targets
            for item in other['items'] for variant in item['variants']
        ):
            raise HTTPException(409, '部分 SKU 被其他未取消批次使用，保留原号码；请先核查相关批次')
    now = datetime.now(timezone.utc).isoformat()
    replacements = {}
    for key, (source, variant) in targets.items():
        # Numeric legacy suffixes are never generated again; cancelled snapshots keep the old IDs.
        db.execute('DELETE FROM platform_skus WHERE store_id=? AND source_key=? AND identity_key=?', (store['id'], *key))
        replacements[key] = platform_sku.allocate(db, store, source, variant, now)
    for item in preview['items']:
        for variant in item['variants']:
            key = platform_sku.identity(item['source'], variant)
            if key in replacements:
                variant['platform_sku'] = replacements[key]
    preview['sku_rule'] = platform_sku.RULE
    preview['preview_hash'] = preview_fingerprint(preview)
    db.execute('UPDATE prelisting_batches SET preview_json=?,updated_at=? WHERE id=?', (encoded(preview), now, current['id']))
    return True


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
        trash=db.execute('SELECT deleted_at FROM prelisting_batch_trash WHERE batch_id=?',(bid,)).fetchone()
        return dict(id=row['id'], status=row['status'], created_at=row['created_at'], updated_at=row['updated_at'],deleted_at=trash[0] if trash else None,
                    preview=json.loads(row['preview_json']), execution_enabled=False)

    @router.get('/api/listing-stores')
    def stores():
        with catalog.connect() as db:
            from .listing_prerequisites import preparation_settings
            result=[]
            for r in db.execute('SELECT * FROM listing_stores ORDER BY lower(name)'):
                bound=db.execute("SELECT * FROM walmart_accounts WHERE store_id=? AND encrypted_credentials<>''",(r['id'],)).fetchone()
                result.append(dict(r) | {'default_brand':preparation_settings(db,bound).get('default_brand','') if bound else ''})
            return result

    @router.post('/api/listing-stores', status_code=201)
    def add_store(data: StoreInput):
        try:
            with catalog.connect() as db:
                write_lock(db)
                code = data.sku_code or platform_sku.available_code(db, data.name)
                if code and db.execute('SELECT 1 FROM listing_stores WHERE sku_code=?', (code,)).fetchone():
                    if data.sku_code:
                        raise HTTPException(409, 'SKU 店铺编码已被其他店铺使用')
                    code = None
                store_id = insert_id(db, "INSERT INTO listing_stores(name,name_key,marketplace_name,origin,sku_code) VALUES(?,?,?,'manual',?)",
                                 (data.name, seller_key(data.name), data.marketplace_name or data.name, code))
                return dict(db.execute('SELECT * FROM listing_stores WHERE id=?', (store_id,)).fetchone())
        except INTEGRITY_ERRORS:
            raise HTTPException(409, '店铺已经存在，请使用已有店铺')

    @router.patch('/api/listing-stores/{store_id}')
    def store_status(store_id: int, data: StoreState):
        with catalog.connect() as db:
            write_lock(db)
            store = db.execute('SELECT * FROM listing_stores WHERE id=?', (store_id,)).fetchone()
            if not store:
                raise HTTPException(404, '店铺不存在')
            if 'sku_code' in data.model_fields_set and data.sku_code != store['sku_code']:
                if db.execute('SELECT 1 FROM platform_skus WHERE store_id=?', (store_id,)).fetchone():
                    raise HTTPException(409, '该店铺已分配 SKU，不能更改其编码')
                if data.sku_code and db.execute('SELECT 1 FROM listing_stores WHERE sku_code=? AND id<>?', (data.sku_code, store_id)).fetchone():
                    raise HTTPException(409, 'SKU 店铺编码已被其他店铺使用')
                db.execute('UPDATE listing_stores SET sku_code=? WHERE id=?', (data.sku_code, store_id))
            if data.active is not None:
                db.execute('UPDATE listing_stores SET active=? WHERE id=?', (int(data.active), store_id))
            return {'ok': True}

    @router.post('/api/prelisting/preview')
    def preview(data: PlanInput):
        with catalog.connect() as db:
            read_snapshot(db)
            return build_preview(catalog, db, data)[0]

    @router.post('/api/prelisting/templates')
    def templates(data: PlanInput):
        from .listing_templates import template_options
        with catalog.connect() as db:
            read_snapshot(db)
            return template_options(db, selected_rows(catalog, db, data), data)

    @router.post('/api/prelisting/batches', status_code=201)
    def create(data: CreateInput, request: Request, background_tasks: BackgroundTasks):
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
            from .listing_prerequisites import pool_assignment
            for item in result['items']:
                for variant in item['variants']:
                    variant['platform_sku'] = platform_sku.allocate(db, result['store'], item['source'], variant, now)
                    assigned=pool_assignment(db,item['source'],variant['sku'],result['brand'],actor(request))
                    if assigned:
                        variant['assigned_identifier']=dict(state='assigned',type=assigned['kind'],value=assigned['value'])
                    elif 'assigned_identifier' in variant:
                        variant['assigned_identifier']=dict(state='exhausted',type='EAN',value='')
            settings = dict(listing_ids=[r['id'] for r in rows], store_id=data.store_id, brand=data.brand, multiplier=str(data.multiplier))
            if data.variant_skus is not None:
                settings['variant_skus'] = data.variant_skus
            if data.template_batches:
                settings['template_batches'] = data.template_batches
            db.execute('INSERT INTO prelisting_batches VALUES(?,?,?,?,?,?,?,?,?)',
                       (bid, str(data.request_key), request_hash, 'draft', now, now, actor(request), encoded(settings), encoded(result)))
            for row, item in zip(rows, result['items']):
                db.execute('INSERT INTO prelisting_items VALUES(?,?,?,?)', (bid,row['id'],row['digest'],row['data_json']))
                db.execute('INSERT INTO prelisting_claims VALUES(?,?,?,?)', (seller_key(item['source']),claim_identity(item),data.store_id,bid))
            event(db, bid, request, 'create', now)
            if data.template_batches:
                from .listing_templates import create_document
                create_document(catalog, db, bid, data, actor(request))
            from .listing_generation_jobs import auto_start
            if not data.template_batches:
                background_tasks.add_task(auto_start,catalog,request.app.state.walmart_publisher,bid,actor(request))
            return batch(db, bid)

    @router.get('/api/prelisting/batches')
    def batches(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                status:Literal['','draft','confirmed','cancelled','review_failed']='',store_id:int=Query(0,ge=0),
                q:str=Query('',max_length=200),trash:bool=False,
                created_from:AwareDatetime | None=None,created_to:AwareDatetime | None=None):
        if created_from and created_to and created_from>created_to:
            raise HTTPException(422,'开始时间不能晚于结束时间')
        with catalog.connect() as db:
            read_snapshot(db)
            store_expr="CAST(b.preview_json AS jsonb)->'store'->>'id'" if catalog.postgres else "CAST(json_extract(b.preview_json,'$.store.id') AS TEXT)"
            brand_expr="CAST(b.preview_json AS jsonb)->>'brand'" if catalog.postgres else "json_extract(b.preview_json,'$.brand')"
            name_expr="CAST(b.preview_json AS jsonb)->'store'->>'name'" if catalog.postgres else "json_extract(b.preview_json,'$.store.name')"
            where=['t.batch_id IS NOT NULL' if trash else 't.batch_id IS NULL'];args=[]
            from .walmart_review import queue_sql
            prefix=queue_sql(catalog.postgres)
            failed="(SELECT count(*) FROM ranked r WHERE r.batch_id=b.id AND r.position=1 AND r.status IN ('failed','rejected','unpublished'))"
            if catalog.postgres:
                doc_rows="jsonb_array_elements(CAST(d.rows_json AS jsonb)) AS v(value)"
                sku="v.value->>'platform_sku'"
            else:
                doc_rows='json_each(d.rows_json) AS v'
                sku="json_extract(v.value,'$.platform_sku')"
            pending=f"(SELECT count(*) FROM walmart_listing_documents d CROSS JOIN {doc_rows} WHERE d.batch_id=b.id AND NOT EXISTS (SELECT 1 FROM expanded e WHERE e.batch_id=b.id AND e.platform_sku={sku}))"
            if not trash:
                where.append(f"(NOT EXISTS (SELECT 1 FROM walmart_listing_attempts a WHERE a.batch_id=b.id) OR {failed}>0 OR {pending}>0)")
            if status=='review_failed':where.append(f'{failed}>0')
            elif status:where.append('b.status=?');args.append(status)
            if store_id:where.append(f'{store_expr}=?');args.append(str(store_id))
            for value,operator in ((created_from,'>='),(created_to,'<=')):
                if value is not None:
                    # Stored timestamps are text; compare instants, including legacy offsets.
                    column='CAST(b.created_at AS timestamptz)' if catalog.postgres else 'julianday(b.created_at)'
                    parameter='CAST(? AS timestamptz)' if catalog.postgres else 'julianday(?)'
                    where.append(f'{column}{operator}{parameter}')
                    args.append(value.astimezone(timezone.utc).isoformat())
            if q.strip():
                where.append(f'(lower(b.id) LIKE ? OR lower({brand_expr}) LIKE ? OR lower({name_expr}) LIKE ?)')
                args.extend(['%'+q.strip().lower()+'%']*3)
            relation=' FROM prelisting_batches b LEFT JOIN prelisting_batch_trash t ON b.id=t.batch_id WHERE '+' AND '.join(where)
            total=db.execute(prefix+'SELECT count(*)'+relation,args).fetchone()[0]
            rows = db.execute(prefix+f'SELECT b.*,t.deleted_at,{failed} AS review_failed_count,{pending} AS unsubmitted_count'+relation+' ORDER BY b.created_at DESC,b.id LIMIT ? OFFSET ?', (*args,page_size,(page-1)*page_size)).fetchall()
            items=[]
            for r in rows:
                p=json.loads(r['preview_json'])
                items.append(dict(id=r['id'],status=r['status'],created_at=r['created_at'],deleted_at=r['deleted_at'],store=p['store']['name'],
                                  brand=p['brand'],multiplier=p['multiplier'],total=p['total'],ready_count=p['ready_count'],variant_count=p['variant_count'],review_failed_count=r['review_failed_count'],unsubmitted_count=r['unsubmitted_count']))
            return dict(items=items,total=total,page=page,page_size=page_size)

    @router.post('/api/prelisting/batches/bulk-action')
    def bulk_action(data:BatchBulkAction,request:Request):
        with catalog.connect() as db:
            write_lock(db)
            # Validate the whole selection first, so mixed-status requests change nothing.
            for bid in data.ids:
                current=batch(db,bid)
                if db.execute("SELECT 1 FROM walmart_listing_attempts WHERE batch_id=? AND state<>'rejected'",(bid,)).fetchone():
                    raise HTTPException(409,'所选批次已有沃尔玛提交记录，请在审核进度中处理，不能直接删除')
            at=datetime.now(timezone.utc).isoformat();changed=0
            for bid in data.ids:
                exists=db.execute('SELECT 1 FROM prelisting_batch_trash WHERE batch_id=?',(bid,)).fetchone()
                if data.action=='delete' and not exists:
                    # End local preparation atomically with hiding the record.
                    # Old workers and browser receipts must never resume deleted batches.
                    db.execute("UPDATE prelisting_batches SET status='cancelled',updated_at=? WHERE id=?",(at,bid))
                    db.execute('DELETE FROM prelisting_claims WHERE batch_id=?',(bid,))
                    db.execute("""UPDATE listing_generation_jobs SET state='interrupted',message=?,updated_at=?
                        WHERE batch_id=? AND state IN ('awaiting_sources','queued','running')""",
                        ('批次已删除，资料生成已停止；已保存内容保留',int(datetime.now(timezone.utc).timestamp()),bid))
                    db.execute('INSERT INTO prelisting_batch_trash VALUES(?,?,?)',(bid,at,actor(request)))
                elif data.action=='restore' and exists:
                    db.execute('DELETE FROM prelisting_batch_trash WHERE batch_id=?',(bid,))
                else:continue
                event(db,bid,request,'trash' if data.action=='delete' else 'restore',at);changed+=1
            return dict(changed=changed)

    @router.get('/api/prelisting/batches/{bid}')
    def detail(bid: str):
        with catalog.connect() as db:
            return batch(db, bid)

    @router.post('/api/prelisting/batches/{bid}/action')
    def action(bid: str, data: BatchAction, request: Request):
        with catalog.connect() as db:
            write_lock(db)
            current=batch(db,bid)
            if current['deleted_at']:
                raise HTTPException(409,'批次已移入回收站，请先恢复记录')
            if data.action in ('cancel','upgrade_skus') and db.execute(
                "SELECT 1 FROM walmart_listing_attempts WHERE batch_id=? AND state<>'rejected'", (bid,)).fetchone():
                raise HTTPException(409, '该批次已有沃尔玛提交记录，不能取消或重新分配 SKU；请在上架资料中修正失败项')
            if data.action == 'upgrade_skus':
                if upgrade_draft_skus(db, current):
                    event(db, bid, request, 'upgrade_skus', datetime.now(timezone.utc).isoformat())
                return batch(db, bid)
            target='confirmed' if data.action == 'confirm' else 'cancelled'
            if current['status'] == target:
                return current
            if current['status'] == 'cancelled':
                raise HTTPException(409, '已取消的批次不能确认，请重新创建')
            if target == 'confirmed':
                if current['preview'].get('sku_rule') != platform_sku.RULE:
                    raise HTTPException(409, '旧批次尚未使用新规则 SKU，请先升级 SKU，或取消后重新创建')
                row=db.execute('SELECT settings_json FROM prelisting_batches WHERE id=?',(bid,)).fetchone()
                refreshed,_=build_preview(catalog,db,PlanInput.model_validate_json(row[0]),exclude_batch=bid)
                if refreshed['preview_hash'] != current['preview']['preview_hash']:
                    raise HTTPException(409,'商品或规则状态已变化，请取消此批次并重新预览创建')
                expected = [(i['listing_id'], v['sku'], v['platform_sku']) for i in refreshed['items'] for v in i['variants']]
                frozen = [(i['listing_id'], v['sku'], v.get('platform_sku')) for i in current['preview']['items'] for v in i['variants']]
                if expected != frozen or any(not sku for _, _, sku in expected):
                    raise HTTPException(409, '批次平台 SKU 与店铺分配记录不一致，请核查')
                if refreshed['ready_count'] != refreshed['total']:
                    raise HTTPException(409,'批次存在待复核项，请补齐来源数据后重新创建')
            now=datetime.now(timezone.utc).isoformat()
            db.execute('UPDATE prelisting_batches SET status=?,updated_at=? WHERE id=?',(target,now,bid))
            if target == 'cancelled':
                db.execute('DELETE FROM prelisting_claims WHERE batch_id=?',(bid,))
            event(db,bid,request,data.action,now)
            return batch(db,bid)

    return router
