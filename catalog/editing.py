"""Field-level local edits layered over immutable upstream evidence."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import Variant, safe_url
from .variant_fields import with_variant_fields
from .database import write_lock


LOCKED_FIELDS = {
    'templetName', 'dc_platformAccount', 'dc_platformSku', 'sku alias', 'ASIN',
    'dc_productSourceUrl', 'dc_sourceUrl', 'record_id', 'template', 'sku', 'asin',
    'Brand Name', 'dc_price', 'dc_initListingQuantity', 'MSRP', 'dc_productId',
    'Title', 'title', 'description', 'Key Features', 'Brand', 'brand',
}
SIMPLE = {'brand', 'seller', 'price', 'stock', 'msrp', 'ean', 'images'}
CHILD_SIMPLE = {'seller', 'price', 'stock', 'ean', 'images', 'product_id_type',
                'swatch_attribute', 'swatch_image', 'shipping_weight'}
COPY = {'copy.title', 'copy.description', 'copy.features'}
MIRRORS = {'brand': 'Brand Name', 'price': 'dc_price', 'stock': 'dc_initListingQuantity',
           'msrp': 'MSRP', 'ean': 'dc_productId'}


def encoded(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS product_edits(
            listing_id TEXT PRIMARY KEY REFERENCES listings(id), revision INTEGER NOT NULL,
            source_digest TEXT NOT NULL, changes_json TEXT NOT NULL,
            updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS product_edit_events(
            id INTEGER PRIMARY KEY, listing_id TEXT NOT NULL REFERENCES listings(id),
            at TEXT NOT NULL, actor_id INTEGER, action TEXT NOT NULL, data_json TEXT NOT NULL);
        CREATE VIEW IF NOT EXISTS current_listings AS
        WITH effective AS (
            SELECT l.*, local_product(l.data_json,e.changes_json,e.revision,e.updated_at,
                                     e.source_digest,l.digest) AS effective_json,
                   COALESCE(e.revision,0) AS edit_version
            FROM listings l LEFT JOIN product_edits e ON e.listing_id=l.id
        )
        SELECT id,product_id,namespace,external_id,sku,store,
            json_extract(effective_json,'$.brand') AS brand,template,uploaded_at,observed_at,evidence_kind,
            json_extract(effective_json,'$.save_succeeded') AS saved,
            json_extract(effective_json,'$.fill_complete') AS complete,
            json_extract(effective_json,'$.readback_verified') AS verified,
            local_search(effective_json) AS search_text,effective_json AS data_json,
            local_digest(effective_json) AS digest,digest AS source_digest,edit_version
        FROM effective;
        INSERT OR IGNORE INTO migrations VALUES(5);
    ''')


def value_at(data, change):
    target = data
    if change.get('variant_sku'):
        target = next((v for v in data['variants'] if v['sku'] == change['variant_sku']), None)
        if target is None:
            raise ValueError('子 SKU 已不存在，请重新加载商品')
    field = change['field']
    if field.startswith('copy.'):
        return target['copy'], field[5:]
    if field in {'fields', 'attributes'}:
        return target[field], change['key']
    return target, field


def local_product(raw, changes_json, revision, updated, edited_source, source_digest):
    data = json.loads(raw)
    data['variants'] = [with_variant_fields(v) for v in data['variants']]
    changes = json.loads(changes_json or '[]')
    review = next((c['value'] for c in changes if c['field'] == '_manual_review'), None)
    changes = [c for c in changes if c['field'] != '_manual_review']
    conflicts = []
    for change in changes:
        try:
            target, key = value_at(data, change)
            target[key] = change['value']
            mirror = MIRRORS.get(change['field'])
            if not change.get('variant_sku') and mirror in data['fields']:
                data['fields'][mirror] = change['value']
        except ValueError:
            conflicts.append(change['variant_sku'])
    data['local_edit'] = dict(
        revision=f'{source_digest}:{revision or 0}', changed=bool(changes),
        updated_at=updated or '', changes=changes, conflicts=conflicts,
        source_changed=bool(changes and edited_source != source_digest),
    )
    if changes:
        data['local_edit']['source_status'] = {k: data[k] for k in (
            'save_succeeded', 'fill_complete', 'readback_verified')}
        data.update(save_succeeded=False, fill_complete=False, readback_verified=False)
    data['manual_review'] = None
    if review:
        valid = review['source_digest'] == source_digest and not conflicts
        data['manual_review'] = {**review, 'valid': valid}
        if valid:
            # Local human assessment only; never assert an ERP save or readback.
            data['manual_review']['source_fill_complete'] = data['fill_complete']
            data['fill_complete'] = review['complete']
    return encoded(data)


def local_search(raw):
    data = json.loads(raw)
    return ' '.join(str(data.get(k, '')) for k in (
        'asin', 'sku', 'platform_sku', 'seller', 'store', 'brand', 'creator', 'template', 'source')
    ).casefold() + ' ' + data['copy']['title'].casefold() + ' ' + ' '.join(
        f"{v['asin']} {v['sku']} {v['seller']}" for v in data['variants']).casefold()


def register(db):
    db.create_function('local_product', 6, local_product, deterministic=True)
    db.create_function('local_search', 1, local_search, deterministic=True)
    db.create_function('local_digest', 1, lambda raw: hashlib.sha256(raw.encode()).hexdigest(), deterministic=True)


class RevisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: str = Field(min_length=1, max_length=100)


class EditInput(RevisionInput):
    field: str = Field(min_length=1, max_length=100)
    variant_sku: str = Field(default='', max_length=200)
    key: str = Field(default='', max_length=300)
    value: Any


class ReviewInput(RevisionInput):
    complete: bool = True
    note: str = Field(default='', max_length=1000)


def validate_change(data, change):
    field, value, sku = change['field'], change['value'], change['variant_sku']
    allowed = CHILD_SIMPLE if sku else SIMPLE
    if field not in allowed | COPY | ({'attributes'} if sku else {'fields'}):
        raise HTTPException(422, '该字段只读，不能修改商品身份或来源证据')
    if field in {'fields', 'attributes'}:
        key = change['key']
        if not key or key in LOCKED_FIELDS or key.casefold() in {k.casefold() for k in LOCKED_FIELDS}:
            raise HTTPException(422, '身份或关联字段只读；文案、价格等请在对应编辑区修改')
        target, _ = value_at(data, change)
        if key not in target:
            raise HTTPException(422, '只能编辑已有属性')
        if target[key] is not None and type(value) is not type(target[key]):
            raise HTTPException(422, '请保留属性的数据类型')
        if len(encoded(value)) > 20000:
            raise HTTPException(422, '属性内容过长')
        if field == 'fields' and key == 'dc_productType' and not isinstance(value, str):
            raise HTTPException(422, '类目必须是文字')
    elif field == 'images':
        try:
            validated = Variant(sku='validation', images=value)
            change['value'] = validated.images
        except ValidationError:
            raise HTTPException(422, '图片需为最多 100 条不含凭据的 HTTP/HTTPS 链接')
    elif field == 'copy.features':
        if not isinstance(value, list) or len(value) > 30 or any(not isinstance(v, str) or len(v) > 10000 for v in value):
            raise HTTPException(422, '卖点需为最多 30 条文字，每条最多 10000 字')
    else:
        if not isinstance(value, str) or len(value) > (50000 if field == 'copy.description' else 2000):
            raise HTTPException(422, '请输入有效文字，内容不能超过长度限制')
        if field in {'price', 'msrp'}:
            try:
                n = Decimal(value)
                if not n.is_finite() or not 0 < n <= 10000000 or n.as_tuple().exponent < -2:
                    raise ValueError()
            except (InvalidOperation, ValueError):
                raise HTTPException(422, '金额必须为大于 0 的数字，最多两位小数')
            change['value'] = f'{n:.2f}'
        elif field == 'stock':
            if value not in {'0', '10'}:
                raise HTTPException(422, '库存仅允许 0 或 10')
        elif field == 'ean':
            if value and (not value.isascii() or not value.isdigit() or len(value) not in ({8, 12, 13, 14} if sku else {13})):
                raise HTTPException(422, '商品编码需为 8、12、13 或 14 位数字；主商品 EAN 为 13 位')
        elif field == 'product_id_type':
            if value not in {'', 'EAN', 'UPC', 'GTIN', 'ISBN'}:
                raise HTTPException(422, '编码类型仅支持 EAN、UPC、GTIN、ISBN')
        elif field == 'swatch_image':
            try:
                change['value'] = safe_url(value.strip())
            except ValueError:
                raise HTTPException(422, '色卡图片需为不含凭据的 HTTP/HTTPS 链接')
        elif field == 'shipping_weight':
            try:
                if value and (not Decimal(value).is_finite() or not 0 < Decimal(value) <= 10000000):
                    raise ValueError()
            except (ValueError, InvalidOperation):
                raise HTTPException(422, '运输重量需为空或大于 0 的数值（lb）')
        elif field in {'brand', 'seller'}:
            if len(value.strip()) > 300:
                raise HTTPException(422, '名称过长')
            change['value'] = value.strip()
    return change


def editing_routes(catalog):
    router = APIRouter(prefix='/api/products')

    def mutate(listing_id, data, request, reset=False, review=False):
        with catalog.connect() as db:
            write_lock(db)
            row = db.execute('SELECT * FROM listings WHERE id=?', (listing_id,)).fetchone()
            if not row:
                raise HTTPException(404, '商品不存在')
            old = db.execute('SELECT * FROM product_edits WHERE listing_id=?', (listing_id,)).fetchone()
            version = old['revision'] if old else 0
            if data.expected_revision != f"{row['digest']}:{version}":
                raise HTTPException(409, '商品已被更新，请刷新后核对再保存；本次输入尚未保存')
            raw = json.loads(row['data_json'])
            raw['variants'] = [with_variant_fields(v) for v in raw['variants']]
            changes = json.loads(old['changes_json']) if old else []
            before = encoded(changes)
            previous_review = next((c['value'] for c in changes if c['field'] == '_manual_review'), None)
            changes = [c for c in changes if c['field'] != '_manual_review']
            user = getattr(request.state, 'user', None)
            if review:
                effective = json.loads(local_product(row['data_json'], encoded(changes), version,
                    old['updated_at'] if old else '', old['source_digest'] if old else '', row['digest']))
                if effective['local_edit']['conflicts']:
                    raise HTTPException(409, '存在已失效的子 SKU 修改，请先处理后再确认完整')
                marker = dict(complete=data.complete, note=data.note.strip(),
                    at=datetime.now(timezone.utc).isoformat(), actor_id=user['id'] if user else None,
                    actor=user['username'] if user else '本机用户', source_digest=row['digest'])
                changes.append(dict(field='_manual_review', variant_sku='', key='', value=marker))
            elif reset:
                changes = []
            else:
                try:
                    change = validate_change(raw, data.model_dump(exclude={'expected_revision'}))
                    target, key = value_at(raw, change)
                except ValueError as exc:
                    raise HTTPException(409, str(exc))
                identity = lambda c: (c['variant_sku'], c['field'], c['key'])
                changes = [c for c in changes if identity(c) != identity(change)]
                if change['value'] != target.get(key):
                    changes.append(change)
                # A no-op edit must not silently invalidate an existing review.
                prior_changes = [c for c in json.loads(before) if c['field'] != '_manual_review']
                if sorted(changes, key=identity) == sorted(prior_changes, key=identity) and previous_review:
                    changes = prior_changes
                    changes.append(dict(field='_manual_review', variant_sku='', key='', value=previous_review))
            if encoded(changes) == before:
                return catalog.get(listing_id)
            now = datetime.now(timezone.utc).isoformat()
            db.execute('''INSERT INTO product_edits VALUES(?,?,?,?,?) ON CONFLICT(listing_id)
                DO UPDATE SET revision=excluded.revision,source_digest=excluded.source_digest,
                    changes_json=excluded.changes_json,updated_at=excluded.updated_at''',
                (listing_id, version + 1, row['digest'], encoded(changes), now))
            db.execute('INSERT INTO product_edit_events(listing_id,at,actor_id,action,data_json) VALUES(?,?,?,?,?)',
                (listing_id, now, user['id'] if user else None, 'review' if review else 'reset' if reset else 'edit',
                 encoded({'before': json.loads(before), 'after': changes})))
            catalog.refresh(db, listing_id)
        return catalog.get(listing_id)

    @router.patch('/{listing_id}/edits')
    def edit(listing_id: str, data: EditInput, request: Request):
        return mutate(listing_id, data, request)

    @router.post('/{listing_id}/edits/reset')
    def reset(listing_id: str, data: RevisionInput, request: Request):
        return mutate(listing_id, data, request, reset=True)

    @router.post('/{listing_id}/review')
    def review(listing_id: str, data: ReviewInput, request: Request):
        return mutate(listing_id, data, request, review=True)

    return router
