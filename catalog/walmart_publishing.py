"""Versioned listing documents and durable, store-scoped Marketplace submissions.

The transaction commits a 'sending' record BEFORE the external POST. A crash or
ambiguous response is never treated as a failed request that can be resent.
"""
import copy
import hashlib
import hmac
import json
import math
import os
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .listing_content import normalize as normalize_content
from .product_store_history import histories
from . import platform_sku
from .listing_titles import branded_title, source_brands
from .blacklist import active_entries, matches
from .credential_vault import decrypt, VaultError
from .database import write_lock, is_postgres
from .object_storage import StorageError
from .prelisting import PlanInput, build_preview, encoded, fingerprint
from .projection import category
from .walmart_accounts import account, now, store
from .walmart_client import WalmartError
from .walmart_publishing_client import SubmissionUnknown
from .walmart_listing_schema import (DEFAULT_VERSION, LABELS, feed_item, identifier_errors,
    image_signature, normalize_spec, schema_errors, sections, taxonomy_options, upstream_reason)

ACTIVE = {'sending', 'unknown', 'processing'}
RETRYABLE = {'failed'}
EDITABLE = {'unsubmitted', 'failed', 'rejected', 'unpublished'}
MAX_SOURCE_AGE = 86400


def initialize(db):
    statements = [
        '''CREATE TABLE IF NOT EXISTS walmart_listing_documents(
            batch_id TEXT PRIMARY KEY REFERENCES prelisting_batches(id), revision INTEGER NOT NULL,
            version TEXT NOT NULL, rows_json TEXT NOT NULL,basis_json TEXT NOT NULL,
            validation_json TEXT NOT NULL,updated_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS walmart_listing_metadata(
            store_id INTEGER NOT NULL REFERENCES listing_stores(id), partner_id TEXT NOT NULL,
            kind TEXT NOT NULL,version TEXT NOT NULL,product_type TEXT NOT NULL,
            data_json TEXT NOT NULL,fetched_at BIGINT NOT NULL,
            PRIMARY KEY(store_id,partner_id,kind,version,product_type))''',
        '''CREATE TABLE IF NOT EXISTS walmart_listing_attempts(
            id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES prelisting_batches(id),
            store_id INTEGER NOT NULL REFERENCES listing_stores(id),partner_id TEXT NOT NULL,
            binding_revision INTEGER NOT NULL,document_revision INTEGER NOT NULL,
            request_key TEXT NOT NULL UNIQUE,request_hash TEXT NOT NULL,
            state TEXT NOT NULL,feed_id TEXT NOT NULL,payload_json TEXT NOT NULL,
            rows_json TEXT NOT NULL,results_json TEXT NOT NULL,message TEXT NOT NULL,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''',
        '''CREATE UNIQUE INDEX IF NOT EXISTS walmart_listing_feed
            ON walmart_listing_attempts(store_id,feed_id) WHERE feed_id<>'' ''',
        '''CREATE TABLE IF NOT EXISTS walmart_listing_claims(
            store_id INTEGER NOT NULL REFERENCES listing_stores(id),platform_sku TEXT NOT NULL,
            batch_id TEXT NOT NULL REFERENCES prelisting_batches(id),
            attempt_id TEXT NOT NULL REFERENCES walmart_listing_attempts(id),
            PRIMARY KEY(store_id,platform_sku))''',
        '''CREATE TABLE IF NOT EXISTS walmart_listing_events(
            id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES prelisting_batches(id),
            actor_id INTEGER,action TEXT NOT NULL,at TEXT NOT NULL,details_json TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS walmart_listing_polls(
            attempt_id TEXT PRIMARY KEY REFERENCES walmart_listing_attempts(id),
            token TEXT NOT NULL,started_at BIGINT NOT NULL)''',
    ]
    for sql in statements:
        db.execute(sql)
    db.execute('INSERT INTO migrations VALUES(16) ON CONFLICT DO NOTHING')


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Identification(Input):
    mode: Literal['identifier', 'exemption'] = 'identifier'
    type: Literal['GTIN', 'UPC', 'EAN', 'ISBN'] = 'EAN'
    value: str = Field(default='', max_length=14)
    submission_id: str = Field(default='', max_length=150)


class ListingRow(Input):
    platform_sku: str = Field(min_length=1, max_length=50)
    listing_id: str = Field(min_length=1, max_length=100)
    source_sku: str = Field(min_length=1, max_length=200)
    product_type: str = Field(default='', max_length=300)
    fulfillment_center_id: str = Field(default='',max_length=100)
    target_stock: int | None = Field(default=None, ge=0, le=999999999, strict=True)
    identification: Identification
    visible: dict[str, Any]
    orderable: dict[str, Any]
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def safe_json(self):
        def check(value,depth=0):
            if depth>15:
                raise ValueError('属性嵌套过深')
            if isinstance(value,float) and not math.isfinite(value):
                raise ValueError('金额和属性数值必须为有限数值')
            if isinstance(value,dict):
                if len(value)>1000:
                    raise ValueError('属性数量过多')
                for key,child in value.items():
                    if len(key)>300:
                        raise ValueError('属性名过长')
                    check(child,depth+1)
            elif isinstance(value,list):
                if len(value)>10000:
                    raise ValueError('属性项目过多')
                for child in value:check(child,depth+1)
        for value in (self.visible,self.orderable,self.extra):check(value)
        return self


class Save(Input):
    revision: int = Field(ge=1)
    version: str = Field(min_length=1, max_length=80, pattern=r'^5\.0\.[A-Za-z0-9_-]+$')
    rows: list[ListingRow] = Field(min_length=1, max_length=1000)


class Revision(Input):
    revision: int = Field(ge=1)


class PriceReview(Revision):
    platform_sku: str = Field(min_length=1, max_length=50)
    original_price: Decimal | None = Field(default=None, gt=0, le=10000000, decimal_places=2)


class Submit(Revision):
    validation_id: str = Field(min_length=32, max_length=64)
    request_key: uuid.UUID
    skus: list[str] = Field(min_length=1, max_length=1000)
    confirm_store_id: int = Field(gt=0)
    confirm_partner_id: str = Field(min_length=1, max_length=300)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.skus)) != len(self.skus):
            raise ValueError('不能重复提交同一 SKU')
        return self


class Reconcile(Input):
    feed_id: str = Field(min_length=1, max_length=300, pattern=r'^[!-~]+$')


def event(db, bid, request, action, details=None):
    db.execute('INSERT INTO walmart_listing_events VALUES(?,?,?,?,?,?)',
               (uuid.uuid4().hex, bid, getattr(request.state, 'user', {}).get('id'), action, now(), encoded(details or {})))


def admin(request):
    if request.app.state.auth and getattr(request.state, 'user', {}).get('role') != 'admin':
        raise HTTPException(403, '正式提交和处理结果同步需要管理员权限')


def batch(db, bid):
    row = db.execute('SELECT * FROM prelisting_batches WHERE id=?', (bid,)).fetchone()
    if not row:
        raise HTTPException(404, '预上架批次不存在')
    if row['status'] == 'cancelled':
        raise HTTPException(409, '批次已取消，不能编辑或提交')
    return dict(row)


def binding(db, store_id, expected_partner=None):
    target = dict(store(db, store_id))
    row = account(db, store_id)
    if not row or not row['encrypted_credentials'] or row['status'] != 'verified':
        raise HTTPException(409, '请先绑定并验证目标沃尔玛店铺')
    if row['marketplace'] != 'US':
        raise HTTPException(409, '加拿大站已支持 API 绑定验证，刊登和商品管理尚未接入，不能调用美国站操作')
    if expected_partner and row['partner_id'] != expected_partner:
        raise HTTPException(409, '当前绑定店铺与原提交店铺不同，已阻止查询或写入')
    return target, dict(row)


def document(db, bid):
    row = db.execute('SELECT * FROM walmart_listing_documents WHERE batch_id=?', (bid,)).fetchone()
    if not row:
        raise HTTPException(409, '请先点击“填写上架资料”准备资料')
    return dict(row)


def revision_check(doc, revision):
    if doc['revision'] != revision:
        raise HTTPException(409, '上架资料已被其他操作修改，请刷新后重试')


def latest_results(db, bid):
    result = {}
    for attempt in db.execute('SELECT * FROM walmart_listing_attempts WHERE batch_id=? ORDER BY created_at,id', (bid,)):
        for row in json.loads(attempt['rows_json']):
            result[row['platform_sku']] = json.loads(attempt['results_json']).get(row['platform_sku'],
                dict(status='unknown', message='提交结果未确认，请查询原批次', errors=[])) | {'attempt_id': attempt['id']}
    return result


def public_attempt(row):
    return {k: row[k] for k in ('id','store_id','partner_id','binding_revision','document_revision',
                                'state','feed_id','message','created_at','updated_at')} | {
        'results': json.loads(row['results_json']), 'skus': [r['platform_sku'] for r in json.loads(row['rows_json'])]}


def source_category_hints(source):
    """Carry actual source labels, never treat a supplier category code as Walmart's."""
    fields = source.get('fields') or {}
    detail = ((source.get('source_facts') or {}).get('giga') or {}).get('detail') or {}
    values = [category(source), fields.get('dc_productType')]
    if source.get('source') in ('GigaB2B', 'GigaB2B_CA'):
        values.extend([detail.get('category'), detail.get('categoryPath')])
    return list(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))


def public_document(db, bid):
    row = db.execute('SELECT * FROM walmart_listing_documents WHERE batch_id=?', (bid,)).fetchone()
    if not row:
        return None
    p = json.loads(db.execute('SELECT preview_json FROM prelisting_batches WHERE id=?', (bid,)).fetchone()[0])
    validation=json.loads(row['validation_json'])
    cached_specs={}
    requirements={}
    account=db.execute('SELECT * FROM walmart_accounts WHERE store_id=?',(p['store']['id'],)).fetchone()
    from .listing_prerequisites import validation_current
    validation['current']=validation_current(db,dict(row),account,validation)
    validation={k:v for k,v in validation.items() if k not in ('payloads','schema_digests')}
    if account:
        for pt in {r['product_type'] for r in json.loads(row['rows_json']) if r['product_type']}:
            schema=cache_value(db,account,'spec',row['version'],pt)
            if schema:
                cached_specs[pt]=dict(sections=sections(schema,pt),digest=fingerprint(schema),labels=LABELS)
                from .listing_generation import required_keys
                for entry in json.loads(row['rows_json']):
                    if entry['product_type']==pt:
                        requirements[entry['platform_sku']]={section:sorted(required_keys(shape,entry.get(section,{})))
                            for section,shape in sections(schema,pt).items() if section in ('visible','orderable')}
    basis = json.loads(row['basis_json'])
    # Older documents lack this hint. Resolve only their own source listing;
    # the browser still checks against the current store's actual taxonomy.
    store_history=histories(db,[i['listing_id'] for i in basis['items']])
    for item in basis['items']:
        item['store_history']=store_history[item['listing_id']]
        if 'category' not in item or 'category_hints' not in item:
            source = db.execute('SELECT data_json FROM current_listings WHERE id=?', (item['listing_id'],)).fetchone()
            data = json.loads(source[0]) if source else {}
            item.setdefault('category', category(data))
            item['category_hints'] = source_category_hints(data)
    attempts=[]
    for a in db.execute('SELECT * FROM walmart_listing_attempts WHERE batch_id=? ORDER BY created_at DESC,id DESC',(bid,)):
        public=public_attempt(a)
        task=db.execute('SELECT state,next_at,tries,last_error FROM listing_result_tasks WHERE attempt_id=?',(a['id'],)).fetchone()
        public['polling']=dict(task) if task else None
        attempts.append(public)
    pool=dict(db.execute("SELECT count(*) AS total,COALESCE(sum(CASE WHEN source_sku='' AND (brand='' OR brand=?) THEN 1 ELSE 0 END),0) AS available FROM listing_real_identifiers",(p['brand'],)).fetchone())
    from .listing_price_review import reviews
    from .listing_variants import synchronize
    groups=synchronize(db,json.loads(row['rows_json']),basis,row['version'],latest_results(db,bid))
    return dict(revision=row['revision'], version=row['version'], rows=json.loads(row['rows_json']),
                variant_groups=groups,
                source_price_reviews=reviews(db,row),
                basis=basis, validation=validation, labels=LABELS,
                store=p['store'], brand=p['brand'], specs=cached_specs, requirements=requirements, updated_at=row['updated_at'], results=latest_results(db, bid),
                attempts=attempts,identifier_pool=pool)


def fresh_basis(catalog, db, record):
    old = json.loads(record['preview_json'])
    prepared, _ = build_preview(catalog, db, PlanInput.model_validate_json(record['settings_json']), exclude_batch=record['id'])
    identities = lambda p: {(i['listing_id'], v['sku'], v.get('platform_sku')) for i in p['items'] for v in i['variants']}
    if identities(old) != identities(prepared) or any(not v.get('platform_sku') for i in prepared['items'] for v in i['variants']):
        raise HTTPException(409, '来源 SKU、平台 SKU 或变体对应关系已改变，请核查后重新创建批次')
    for item in prepared['items']:
        source=json.loads(db.execute('SELECT data_json FROM current_listings WHERE id=?',(item['listing_id'],)).fetchone()[0])
        item['source_brands'] = source_brands(source)
        item['category'] = category(source)
        item['category_hints'] = source_category_hints(source)
        for v in item['variants']:
            if not v.get('content') and not source.get('variants') and v['sku']==source['sku']:
                v['content']={k:source.get(k) for k in ('copy','images','ean','product_id_type','shipping_weight','seller','source_url')}
    return prepared


def default_rows(preview):
    result = []
    for item in preview['items']:
        for v in item['variants']:
            c = v.get('content') or {}
            copy_data = c.get('copy') or {}
            images = c.get('images') or []
            code = str(c.get('ean') or '')
            code_type = ('UPC' if len(code) == 12 else 'EAN' if len(code) == 13
                         else c.get('product_id_type') or 'GTIN') if code else 'EAN'
            if code_type not in ('UPC','GTIN','EAN','ISBN'):
                code_type = 'GTIN' if code else 'EAN'
            allocated=v.get('assigned_identifier')
            if allocated is not None:
                code,code_type=allocated.get('value',''),allocated.get('type','EAN')
            orderable = {}
            if v.get('price') is not None:
                orderable['price'] = float(v['price'])
                if item.get('msrp'):
                    orderable['msrp'] = float(item['msrp'])
            if c.get('shipping_weight'):
                try:
                    weight = Decimal(str(c['shipping_weight']))
                    if weight.is_finite() and weight > 0:
                        orderable['ShippingWeight'] = float(weight)
                except Exception:
                    pass
            visible = dict(productName=branded_title(copy_data.get('title') or '',preview['brand'],item.get('source_brands',[])), brand=preview['brand'],
                           shortDescription=copy_data.get('description') or '', keyFeatures=copy_data.get('features') or [],
                           mainImageUrl=images[0] if images else '')
            if len(images) > 1:
                visible['productSecondaryImageURL'] = images[1:]
            from .listing_content import image_policy
            image_policy(visible, default_selection=True)
            normalize_content(visible)
            result.append(dict(platform_sku=v['platform_sku'], listing_id=item['listing_id'],source_sku=v['sku'],
                product_type='',fulfillment_center_id='', identification=dict(mode='identifier',type=code_type,value=code,submission_id=''),
                visible=visible,orderable=orderable,extra={}))
    if len(result) > 1000:
        raise HTTPException(422, '正式上架每批最多 1000 个 SKU，请拆分批次')
    return result


def cache_value(db, binding_row, kind, version='', pt='', age=86400):
    r = db.execute('''SELECT * FROM walmart_listing_metadata WHERE store_id=? AND partner_id=?
        AND kind=? AND version=? AND product_type=?''', (binding_row['store_id'],binding_row['partner_id'],kind,version,pt)).fetchone()
    return json.loads(r['data_json']) if r and r['fetched_at'] > time.time()-age else None


def cache_put(db, account_row, kind, value, version='', pt=''):
    db.execute('''INSERT INTO walmart_listing_metadata VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(store_id,partner_id,kind,version,product_type)
        DO UPDATE SET data_json=excluded.data_json,fetched_at=excluded.fetched_at''',
        (account_row['store_id'],account_row['partner_id'],kind,version,pt,encoded(value),int(time.time())))


def normalize_nodes(raw):
    if not isinstance(raw,list):
        raise WalmartError('沃尔玛发货仓响应格式异常')
    return [dict(id=str(n['shipNode']),name=n.get('shipNodeName') or str(n['shipNode']),type=n.get('nodeType',''))
            for n in raw if isinstance(n,dict) and n.get('status')=='ACTIVE' and n.get('shipNode')]


def unchanged_sources(db, doc):
    basis = json.loads(doc['basis_json'])
    for item in basis['items']:
        current = db.execute('SELECT digest FROM current_listings WHERE id=?', (item['listing_id'],)).fetchone()
        if not current or current['digest'] != item['source_digest']:
            raise HTTPException(409, '商品库来源价格、库存或内容已变化，请刷新来源并重新校验')
    return basis


def source_issues(item, variant, price_verified=False, target_stock=None):
    # Presentation gaps can be filled in this document. Core evidence cannot be overridden here.
    ignored = ('缺少标题，', '缺少描述，', '缺少卖点，', '缺少商品图片，', '缺少UPC / GTIN', '来源类目缺失，')
    errors = [e for e in item['errors'] if not e.startswith(ignored)]
    from .listing_price_review import eligible, MISSING
    # Each row owns its source-price warning; another SKU's acknowledgement cannot clear it.
    other_prices = {f"{v['sku']}：{MISSING}" for v in item.get('variants', []) if v.get('sku') != variant.get('sku') and eligible(item,v)}
    errors = [e for e in errors if e not in other_prices]
    if target_stock is not None:
        errors = [e for e in errors if not (
            e.startswith(('Seller 可售库存缺失或异常', 'Seller 可售库存为 ', '赛盈可用库存为 ', '赛盈库存缺失'))
            or '：库存缺失或不符合 0 / 10 规则' in e)]
    verified = price_verified and eligible(item,variant)
    if verified:
        errors = [e for e in errors if e != f"{variant['sku']}：{MISSING}"]
    if variant.get('price') is None and not verified:
        errors.append('缺少已核验的来源售价，不能提交')
    if target_stock is None and str(variant.get('stock')) != '10':
        errors.append('当前无可上架库存或库存未核验，请先更新来源库存')
    try:
        age=time.time() - datetime.fromisoformat(item['observed_at']).timestamp()
        if age < -300:
            errors.append('来源核验时间晚于当前时间，请核对来源时间')
    except (ValueError, TypeError):
        errors.append('来源核验时间无效，请重新采集／同步')
    return list(dict.fromkeys(errors))


def approved_exemption(session, row, category_name, submission_id):
    approval=session.exemptions(row['product_type'],str(row['visible'].get('brand','')),submission_id)
    expected=dict(productType=row['product_type'],brand=row['visible'].get('brand',''),
                  submissionId=submission_id,exemptionStatus='APPROVED',category=category_name)
    return any(all(str(a.get(k,'')).strip().casefold()==str(v).strip().casefold() for k,v in expected.items())
               for a in approval.get('gtinExemptions',[]) if isinstance(a,dict))


def feedback_errors(db,doc,row,spec):
    """Keep unresolved ingestion/publication feedback tied to the submitted SKU."""
    from .walmart_listing_schema import primary_image_error
    attempts=db.execute('SELECT * FROM walmart_listing_attempts WHERE batch_id=? ORDER BY created_at DESC,id DESC',(doc['batch_id'],)).fetchall()
    sku=row['platform_sku']
    for a in attempts:
        originals=[r for r in json.loads(a['rows_json']) if r['platform_sku']==sku]
        if not originals:
            continue
        result=json.loads(a['results_json']).get(sku,{})
        if not (result.get('status')=='unpublished' or
                (result.get('status')=='failed' and result.get('ingestion_status')=='DATA_ERROR')):
            return []
        original=originals[0];issues=[]
        for error in result.get('errors',[]):
            text=error.get('original','');matched=[]
            for section,shape in sections(spec,row['product_type']).items():
                if section not in ('visible','orderable'):
                    continue
                for key in shape.get('properties',{}):
                    if (re.search(r'(?<![a-zA-Z0-9_])'+re.escape(key)+r'(?![a-zA-Z0-9_])',text,re.I)
                            or section=='visible' and key=='mainImageUrl' and primary_image_error(text)):
                        matched.append((section,key))
            for section,key in matched:
                if original.get(section,{}).get(key)==row[section].get(key):
                    issues.append(dict(path=section+'.'+key,message='平台处理反馈：'+error['message']+'；'+text[:400]))
            if not matched and all(row[k]==original.get(k) for k in ('visible','orderable','product_type','identification','extra')):
                issues.append(dict(path='',message='平台退回原因尚未处理：'+error['message']))
        return issues
    return []


def check_rows(db, doc, session, binding_row, skus=None):
    basis = unchanged_sources(db, doc)
    if any(i['source'] == 'GigaB2B_CA' for i in basis['items']):
        raise HTTPException(409, '加拿大云仓商品不能通过 Walmart 美国站接口提交')
    by_sku = {v['platform_sku']: (i,v) for i in basis['items'] for v in i['variants']}
    results, payloads, schema_digests = {}, {}, {}
    taxonomy = cache_value(db,binding_row,'taxonomy',age=3600)
    if taxonomy is None:
        raise HTTPException(409, '请先获取沃尔玛实际类目列表')
    types = {t['name']:t for t in taxonomy}
    nodes=cache_value(db,binding_row,'nodes',age=3600) or []
    from .listing_price_review import reviews, eligible
    price_reviews=reviews(db,doc)
    from .listing_variants import synchronize, POLICY
    groups=synchronize(db,json.loads(doc['rows_json']),basis,doc['version'],latest_results(db,doc['batch_id']))
    group_errors={sku:g['errors'] for g in groups for sku in g['skus']}
    for row in json.loads(doc['rows_json']):
        if skus is not None and row['platform_sku'] not in skus:
            continue
        sku = row['platform_sku']; item, variant = by_sku[sku]
        from .listing_prerequisites import evidence_errors, identifier_ownership_errors
        errors = evidence_errors(db,doc,row,item) + group_errors.get(sku,[])
        for message in source_issues(item,variant,sku in price_reviews,row.get('target_stock')):
            issue=dict(path='',message=message)
            if eligible(item,variant) and message in (f"{variant['sku']}：{variant['error']}", '缺少已核验的来源售价，不能提交'):
                issue.update(path='orderable.price',code='source_price_review')
            errors.append(issue)
        errors.extend(dict(path='identification',message=e) for e in identifier_errors(row['identification']))
        errors.extend(identifier_ownership_errors(db,doc,row,item))
        if row['fulfillment_center_id'] not in {n['id'] for n in nodes}:
            errors.append(dict(path='fulfillment_center_id',message='请选择该沃尔玛店铺实际启用的发货仓'))
        if row['visible'].get('brand')!=basis['brand']:
            errors.append(dict(path='visible.brand',message='品牌与本批次目标品牌不一致，请核对'))
        for field,urls in (('mainImageUrl',[row['visible'].get('mainImageUrl')]),
                           ('productSecondaryImageURL',row['visible'].get('productSecondaryImageURL',[])),
                           ('swatchImages',[s.get('swatchImageUrl') for s in row['visible'].get('swatchImages',[]) if isinstance(s,dict)]
                            if isinstance(row['visible'].get('swatchImages',[]),list) else [])):
            for url in urls if isinstance(urls,list) else []:
                local=re.fullmatch(r'/api/products/images/assets/([a-f0-9]{64})',url) if isinstance(url,str) else None
                if local:
                    found=db.execute('SELECT 1 FROM image_objects WHERE digest=?',(local[1],)).fetchone()
                    if not found and not is_postgres(db):
                        found=db.execute('SELECT 1 FROM image_assets WHERE digest=?',(local[1],)).fetchone()
                    if not found:
                        errors.append(dict(path='visible.'+field,message='本地图片不存在，请重新上传真实图片'))
        pt = row['product_type']
        schema = cache_value(db,binding_row,'spec',doc['version'],pt)
        if pt not in types:
            errors.append(dict(path='product_type',message='请选择当前沃尔玛实际存在的商品类型'))
        if schema is None:
            errors.append(dict(path='product_type',message='请加载该类目的最新官方规格'))
        else:
            errors.extend(feedback_errors(db,doc,row,schema))
            schema_digests[pt] = fingerprint(schema)
            exempt = False
            if row['identification']['mode'] == 'exemption' and pt in types:
                exempt = approved_exemption(session,row,types[pt]['category'],row['identification']['submission_id'])
                if not exempt:
                    errors.append(dict(path='identification',message='未查到该店铺、品牌及类目的已批准豁免，不能用申请中或其他店铺的豁免'))
            try:
                from .listing_vision import stable_feed_row
                entry, validation_schema = feed_item(stable_feed_row(db,row),schema,doc['version'],os.environ.get('CATALOG_PUBLIC_ORIGIN',''),exemption=exempt,
                    stock=row.get('target_stock') if row.get('target_stock') is not None else int(variant['stock']) if str(variant.get('stock')) in ('0','10') else None)
                errors.extend(schema_errors(validation_schema,entry,doc['version']))
                from .listing_skill_defaults import enforce_origin
                origin_check=copy.deepcopy(row)
                errors.extend(enforce_origin(origin_check,sections(schema,row['product_type'])))
                for section in ('visible','orderable'):
                    for key,value in origin_check[section].items():
                        if value!=row[section].get(key):
                            errors.append(dict(path=section+'.'+key,message='原产国须按用户固定规则填写 China，请重新保存资料'))
                payloads[sku] = entry
            except (ValueError,VaultError) as exc:
                errors.append(dict(path='visible.mainImageUrl',message=str(exc)))
        results[sku] = dict(valid=not errors,errors=errors)
    from .listing_prerequisites import resource_digest
    return dict(variant_policy=POLICY,feedback_policy='publication_feedback_v1',content_policy='default8_manual_images_copy_origin_v3',title_policy='brand_prefix_v1',feedback_digest=fingerprint(latest_results(db,doc['batch_id'])),resources_digest=resource_digest(db,binding_row),id=uuid.uuid4().hex,revision=doc['revision'],at=int(time.time()),binding_revision=binding_row['revision'],
                partner_id=binding_row['partner_id'],rows=results,schema_digests=schema_digests,payloads=payloads,
                valid_count=sum(r['valid'] for r in results.values()))


def normalize_titles(db, rows, basis, latest, version=None):
    changes=[]
    for row in rows:
        if latest.get(row['platform_sku'],{}).get('status','unsubmitted') not in EDITABLE:
            continue
        source=db.execute('SELECT data_json FROM current_listings WHERE id=?',(row['listing_id'],)).fetchone()
        brands=source_brands(json.loads(source[0])) if source else []
        old_content=copy.deepcopy(row['visible'])
        old_orderable=copy.deepcopy(row['orderable'])
        origin_audit={}
        if version:
            bound=db.execute('SELECT * FROM walmart_accounts WHERE store_id=?',(basis['store']['id'],)).fetchone()
            schema=cache_value(db,bound,'spec',version,row['product_type']) if bound else None
            if schema:
                from .listing_skill_defaults import enforce_origin
                enforce_origin(row,sections(schema,row['product_type']),origin_audit)
        from .listing_templates import replace_brand
        for field in ('shortDescription','longDescription','keyFeatures'):
            if field in row['visible']:
                row['visible'][field]=replace_brand(row['visible'][field],brands,basis['brand'])
        normalize_content(row['visible'])
        previous=row['visible'].get('productName','')
        title=branded_title(previous,basis['brand'],brands)
        if title!=previous:
            row['visible']['productName']=title
        if row['visible']!=old_content or row['orderable']!=old_orderable:
            changes.append(dict(platform_sku=row['platform_sku'],before=previous,after=title,
                content_before=old_content,content_after=copy.deepcopy(row['visible']),
                orderable_before=old_orderable,orderable_after=copy.deepcopy(row['orderable']),origin_rule=origin_audit))
    return changes


def normalize_document_titles(db, doc):
    """Caller holds write lock. Explicit user rule also applies to existing drafts."""
    basis=unchanged_sources(db,doc)
    rows=json.loads(doc['rows_json'])
    changes=normalize_titles(db,rows,basis,latest_results(db,doc['batch_id']),doc['version'])
    from .listing_variants import synchronize
    before_groups=copy.deepcopy(rows)
    synchronize(db,rows,basis,doc['version'],latest_results(db,doc['batch_id']),apply=True)
    group_changes=[dict(platform_sku=after['platform_sku'],before=before['visible'],after=after['visible'])
                   for before,after in zip(before_groups,rows) if before['visible']!=after['visible']]
    if changes or encoded(rows)!=doc['rows_json']:
        db.execute('UPDATE walmart_listing_documents SET rows_json=?,revision=revision+1,validation_json=?,updated_at=? WHERE batch_id=?',
            (encoded(rows),'{}',now(),doc['batch_id']))
        db.execute('INSERT INTO walmart_listing_events VALUES(?,?,?,?,?,?)',
            (uuid.uuid4().hex,doc['batch_id'],None,'variant_group_rule' if group_changes else 'title_brand_rule',now(),
             encoded({'rule':'target_brand_and_listing_content','changes':changes,'variant_changes':group_changes})))
    return document(db,doc['batch_id'])


def validate_document(catalog, bid, revision, remote, bound):
    """Shared, read-only upstream validation with guarded persistence afterward."""
    with catalog.connect() as db:
        write_lock(db);doc=document(db,bid);revision_check(doc,revision)
        doc=normalize_document_titles(db,doc)
        revision=doc['revision']
    with catalog.connect() as db:
        report=check_rows(db,doc,remote,bound)
    with catalog.connect() as db:
        write_lock(db);batch(db,bid);current=document(db,bid);revision_check(current,revision)
        unchanged_sources(db,current)
        _,live=binding(db,bound['store_id'],bound['partner_id'])
        from .listing_prerequisites import resource_digest
        if live['revision']!=bound['revision'] or resource_digest(db,live)!=report['resources_digest']:
            raise HTTPException(409,'店铺绑定或生成前提变化，请重新校验')
        db.execute('UPDATE walmart_listing_documents SET validation_json=?,updated_at=? WHERE batch_id=?',
                   (encoded(report),now(),bid))
    return report


def attempt(db, bid, aid):
    r = db.execute('SELECT * FROM walmart_listing_attempts WHERE id=? AND batch_id=?',(aid,bid)).fetchone()
    if not r:
        raise HTTPException(404,'提交记录不存在')
    return dict(r)


def assert_not_sending(db, store_id):
    if db.execute("SELECT 1 FROM walmart_listing_attempts WHERE store_id=? AND state='sending'",(store_id,)).fetchone():
        raise HTTPException(409,'该店铺有正在提交或中断的请求，请先查询／核对原提交记录')
    if db.execute('''SELECT 1 FROM walmart_listing_polls AS p JOIN walmart_listing_attempts AS a ON a.id=p.attempt_id
        WHERE a.store_id=? AND p.started_at>?''',(store_id,int(time.time())-180)).fetchone():
        raise HTTPException(409,'该店铺正在同步上架结果及库存，请稍后再修改绑定或提交')


def publishing_routes(catalog):
    router = APIRouter(prefix='/api/prelisting/batches/{bid}/walmart')

    @router.get('')
    def read(bid: str):
        with catalog.connect() as db:
            batch(db,bid)
            return public_document(db,bid)

    @router.post('/prepare')
    def prepare(bid: str, request: Request):
        with catalog.connect() as db:
            write_lock(db); current=batch(db,bid)
            existing=public_document(db,bid)
            if existing:
                if not db.execute("SELECT 1 FROM listing_generation_jobs WHERE batch_id=? AND state IN ('queued','running')",(bid,)).fetchone():
                    from .listing_prerequisites import allocate_document_identifiers
                    normalize_document_titles(db,document(db,bid))
                    allocate_document_identifiers(db,document(db,bid),getattr(request.state,'user',{}).get('id'))
                    return public_document(db,bid)
                return existing
            basis=fresh_basis(catalog,db,current)
            rows=default_rows(basis)
            version=os.environ.get('CATALOG_WALMART_SPEC_VERSION',DEFAULT_VERSION)
            db.execute('INSERT INTO walmart_listing_documents VALUES(?,?,?,?,?,?,?)',
                (bid,1,version,encoded(rows),encoded(basis),'{}',now()))
            from .listing_prerequisites import allocate_document_identifiers
            allocate_document_identifiers(db,document(db,bid),getattr(request.state,'user',{}).get('id'))
            event(db,bid,request,'prepare')
            return public_document(db,bid)

    @router.post('/identifiers/allocate')
    def allocate_identifiers(bid:str,data:Revision,request:Request):
        with catalog.connect() as db:
            write_lock(db);batch(db,bid);doc=document(db,bid);revision_check(doc,data.revision)
            if db.execute("SELECT 1 FROM listing_generation_jobs WHERE batch_id=? AND state IN ('queued','running')",(bid,)).fetchone():
                raise HTTPException(409,'资料正在生成，请结束后再分配编码')
            from .listing_prerequisites import allocate_document_identifiers
            result=allocate_document_identifiers(db,doc,getattr(request.state,'user',{}).get('id'))
            return result | {'document':public_document(db,bid)}

    @router.patch('')
    def save(bid: str, data: Save, request: Request):
        with catalog.connect() as db:
            write_lock(db);batch(db,bid);doc=document(db,bid);revision_check(doc,data.revision)
            rows=[r.model_dump() for r in data.rows];old=json.loads(doc['rows_json'])
            for r in rows:
                if r.get('target_stock') is None:
                    r.pop('target_stock',None)
            key=lambda r:(r['platform_sku'],r['listing_id'],r['source_sku'])
            if len({key(r) for r in rows})!=len(rows) or {key(r) for r in old}!={key(r) for r in rows}:
                raise HTTPException(409,'不能修改 SKU、店铺及商品对应关系，或增删批次商品')
            latest=latest_results(db,bid)
            old_map={r['platform_sku']:r for r in old}
            for r in rows:
                if latest.get(r['platform_sku'],{}).get('status','unsubmitted') not in EDITABLE and r!=old_map[r['platform_sku']]:
                    raise HTTPException(409,'正在处理、结果未知或已成功的商品不能修改；请先查询处理结果')
                if any(k in r['orderable'] for k in ('sku','productIdentifiers','inventory','SkuUpdate','ProductIdUpdate')) or any(k in r['extra'] for k in ('Visible','Orderable')):
                    raise HTTPException(422,'店铺 SKU 和商品编码只能通过专用字段设置')
            rule_changes=normalize_titles(db,rows,json.loads(doc['basis_json']),latest,data.version)
            from .listing_variants import synchronize
            synchronize(db,rows,json.loads(doc['basis_json']),data.version,latest,apply=True)
            if data.version!=doc['version'] and latest:
                raise HTTPException(409,'已有提交记录的批次不能切换规格版本，请保留原版本')
            raw=encoded(rows)
            if len(raw.encode())>12*1024*1024:
                raise HTTPException(413,'上架资料过大，请拆分批次')
            if raw!=doc['rows_json'] or data.version!=doc['version']:
                db.execute('UPDATE walmart_listing_documents SET revision=revision+1,version=?,rows_json=?,validation_json=?,updated_at=? WHERE batch_id=?',
                    (data.version,raw,'{}',now(),bid))
                event(db,bid,request,'edit',{'revision':doc['revision']+1,'before':old,'after':rows,'rule_changes':rule_changes})
            return public_document(db,bid)

    @router.post('/verify-source-price')
    def verify_source_price(bid: str, data: PriceReview, request: Request):
        from .listing_price_review import ACTION, eligible, review_key, reviews
        with catalog.connect() as db:
            write_lock(db);batch(db,bid);doc=document(db,bid);revision_check(doc,data.revision)
            if db.execute("SELECT 1 FROM listing_generation_jobs WHERE batch_id=? AND state IN ('queued','running')",(bid,)).fetchone():
                raise HTTPException(409,'资料正在生成，请结束后再确认')
            if latest_results(db,bid).get(data.platform_sku,{}).get('status','unsubmitted') not in EDITABLE:
                raise HTTPException(409,'该商品已提交或正在处理，不能修改验证记录')
            basis=unchanged_sources(db,doc)
            rows=json.loads(doc['rows_json'])
            row=next((r for r in rows if r['platform_sku']==data.platform_sku),None)
            if not row:
                raise HTTPException(404,'商品不属于当前批次')
            item=next(i for i in basis['items'] if i['listing_id']==row['listing_id'])
            variant=next(v for v in item['variants'] if v['platform_sku']==row['platform_sku'])
            if not eligible(item,variant):
                raise HTTPException(409,'只有缺少来源原价的提醒可以人工确认，其他来源异常请单独处理')
            audit={}
            if data.original_price is not None:
                from .prelisting import money
                from .listing_preparation_source import positive
                try:
                    multiplier=money(basis['multiplier'])
                    price=money((data.original_price*multiplier).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
                except ValueError as exc:
                    raise HTTPException(422,'原价乘倍率后的售价无效：'+str(exc)) from None
                row['orderable']['price']=float(price)
                if not positive(row['orderable'].get('msrp')) and price*2<=Decimal('10000000'):
                    row['orderable']['msrp']=float(price*2)
                audit=dict(original_price=str(data.original_price),multiplier=str(multiplier),method='manual_original')
            key=review_key(basis,item,variant,row)
            if not key:
                raise HTTPException(422,'请先填写并保存有效的上架售价，再点击已验证')
            previous=reviews(db,doc).get(data.platform_sku)
            if not previous or rows!=json.loads(doc['rows_json']) or any(previous.get(k)!=v for k,v in audit.items()):
                event(db,bid,request,ACTION,dict(key=key,platform_sku=row['platform_sku'],
                    source_sku=row['source_sku'],store_id=basis['store']['id'],price=row['orderable']['price'],
                    note='人工确认当前上架售价；解除缺少来源原价限制，不改写来源证据',**audit))
                db.execute('UPDATE walmart_listing_documents SET revision=revision+1,rows_json=?,validation_json=?,updated_at=? WHERE batch_id=?',
                           (encoded(rows),'{}',now(),bid))
            return public_document(db,bid)

    @router.post('/refresh-source')
    def refresh_source(bid: str,data: Revision,request: Request):
        with catalog.connect() as db:
            write_lock(db);current=batch(db,bid);doc=document(db,bid);revision_check(doc,data.revision)
            if db.execute("SELECT 1 FROM walmart_listing_attempts WHERE batch_id=? AND state IN ('sending','unknown','processing')",(bid,)).fetchone():
                raise HTTPException(409,'存在未结束的提交，请先查询处理结果')
            basis=fresh_basis(catalog,db,current);latest=latest_results(db,bid)
            defaults={r['platform_sku']:r for r in default_rows(basis)}
            rows=json.loads(doc['rows_json'])
            for r in rows:
                if latest.get(r['platform_sku'],{}).get('status','unsubmitted') in EDITABLE:
                    if 'price' in defaults[r['platform_sku']]['orderable']:
                        r['orderable']['price']=defaults[r['platform_sku']]['orderable']['price']
                    else:
                        r['orderable'].pop('price',None)
            # MSRP is an editable suggestion; refreshing source prices preserves it.
            db.execute('UPDATE walmart_listing_documents SET revision=revision+1,rows_json=?,basis_json=?,validation_json=?,updated_at=? WHERE batch_id=?',
                (encoded(rows),encoded(basis),'{}',now(),bid))
            event(db,bid,request,'refresh_source',{'previous_basis':fingerprint(json.loads(doc['basis_json'])),'new_basis':fingerprint(basis)})
            return public_document(db,bid)

    def context(request,bid,expected_partner=None):
        with catalog.connect() as db:
            b=batch(db,bid);p=json.loads(b['preview_json'])
            _,bound=binding(db,p['store']['id'],expected_partner)
        return bound,request.app.state.walmart_publisher.session(decrypt(bound['store_id'],bound['encrypted_credentials']),bound['partner_id'])

    @router.post('/taxonomy')
    def taxonomy(bid: str,request: Request):
        try:
            bound,session=context(request,bid)
            with catalog.connect() as db:
                value=cache_value(db,bound,'taxonomy',age=3600)
            if value is None:
                with session as remote:
                    value=taxonomy_options(remote.taxonomy())
                with catalog.connect() as db:
                    write_lock(db);_,current=binding(db,bound['store_id'])
                    if current['revision']!=bound['revision']:
                        raise HTTPException(409,'店铺绑定已变化，请刷新')
                    cache_put(db,bound,'taxonomy',value)
            return {'items':value}
        except (WalmartError,VaultError) as exc:
            raise HTTPException(400,str(exc)) from None

    @router.post('/spec')
    def spec(bid: str,data: ListingRow,request: Request):
        try:
            bound,session=context(request,bid)
            with catalog.connect() as db:
                doc=document(db,bid)
                if data.platform_sku not in {r['platform_sku'] for r in json.loads(doc['rows_json'])}:
                    raise HTTPException(409,'SKU 不属于该批次')
                types=cache_value(db,bound,'taxonomy',age=3600)
                if not types or data.product_type not in {t['name'] for t in types}:
                    raise HTTPException(422,'请选择实际沃尔玛类目，请先获取类目列表')
                value=cache_value(db,bound,'spec',doc['version'],data.product_type)
            if value is None:
                with session as remote:
                    value=normalize_spec(remote.spec(data.product_type,doc['version']),data.product_type,doc['version'])
                with catalog.connect() as db:
                    write_lock(db);_,current=binding(db,bound['store_id'])
                    if current['revision']!=bound['revision']:
                        raise HTTPException(409,'店铺绑定已变化，请刷新')
                    cache_put(db,bound,'spec',value,doc['version'],data.product_type)
            return dict(sections=sections(value,data.product_type),digest=fingerprint(value),labels=LABELS)
        except (WalmartError,VaultError) as exc:
            raise HTTPException(400,str(exc)) from None

    @router.post('/nodes')
    def nodes(bid: str,request: Request):
        try:
            bound,session=context(request,bid)
            with catalog.connect() as db:
                value=cache_value(db,bound,'nodes',age=3600)
            if value is None:
                with session as remote:
                    raw=remote.nodes()
                value=normalize_nodes(raw)
                with catalog.connect() as db:
                    write_lock(db);_,current=binding(db,bound['store_id'])
                    if current['revision']!=bound['revision']:
                        raise HTTPException(409,'店铺绑定已变化，请刷新')
                    cache_put(db,bound,'nodes',value)
            return {'items':value}
        except (WalmartError,VaultError) as exc:
            raise HTTPException(400,str(exc)) from None

    @router.post('/validate')
    def validate(bid: str,data: Revision,request: Request):
        try:
            bound,session=context(request,bid)
            with session as remote:
                report=validate_document(catalog,bid,data.revision,remote,bound)
            with catalog.connect() as db:
                event(db,bid,request,'validate',{'valid_count':report['valid_count'],'revision':data.revision})
                return public_document(db,bid)
        except (WalmartError,VaultError) as exc:
            raise HTTPException(400,str(exc)) from None

    @router.post('/submit')
    def submit(bid: str,data: Submit,request: Request):
        admin(request)
        request_hash=fingerprint(data.model_dump(mode='json'))
        with catalog.connect() as db:
            prior=db.execute('SELECT * FROM walmart_listing_attempts WHERE request_key=?',(str(data.request_key),)).fetchone()
            if prior:
                if prior['batch_id']!=bid or prior['request_hash']!=request_hash:
                    raise HTTPException(409,'提交请求编号已使用且参数不同，请刷新')
                return public_attempt(prior)
        try:
            bound,_=context(request,bid)
            credentials=decrypt(bound['store_id'],bound['encrypted_credentials'])
            with request.app.state.walmart_publisher.session(credentials,bound['partner_id'],writing=True) as remote:
                aid=uuid.uuid4().hex
                with catalog.connect() as db:
                    write_lock(db);b=batch(db,bid);doc=document(db,bid);revision_check(doc,data.revision)
                    # Recheck after acquiring the durable write lock (double click / concurrent workers).
                    prior=db.execute('SELECT * FROM walmart_listing_attempts WHERE request_key=?',(str(data.request_key),)).fetchone()
                    if prior:
                        if prior['request_hash']!=request_hash or prior['batch_id']!=bid:
                            raise HTTPException(409,'请求编号冲突')
                        return public_attempt(prior)
                    basis=unchanged_sources(db,doc);_,current_binding=binding(db,bound['store_id'])
                    report=json.loads(doc['validation_json'])
                    from .listing_prerequisites import validation_current
                    if (report.get('id')!=data.validation_id or not validation_current(db,doc,current_binding,report)
                        or current_binding['revision']!=bound['revision']):
                        raise HTTPException(409,'资料、来源、规格或店铺配置已变化，请重新校验')
                    if data.confirm_store_id!=bound['store_id'] or data.confirm_partner_id!=bound['partner_id']:
                        raise HTTPException(409,'确认店铺与批次绑定不一致，未提交')
                    assert_not_sending(db,bound['store_id'])
                    latest=latest_results(db,bid);all_rows={r['platform_sku']:r for r in json.loads(doc['rows_json'])}
                    from .listing_variants import context as variant_context, plan as variant_plan, submission_errors
                    variant_state=variant_context(db,list(all_rows.values()),basis,doc['version'])
                    groups=variant_plan(list(all_rows.values()),basis,latest=latest,**variant_state)
                    group_errors=submission_errors(groups,data.skus,latest,variant_state['history'])
                    if group_errors:
                        raise HTTPException(409,'；'.join(group_errors))
                    from .listing_price_review import reviews
                    price_reviews=reviews(db,doc)
                    chosen=[];entries=active_entries(db)
                    for sku in data.skus:
                        if sku not in all_rows or not report['rows'].get(sku,{}).get('valid'):
                            raise HTTPException(409,'所选 SKU 尚未通过资料校验')
                        if latest.get(sku,{}).get('status','unsubmitted') not in EDITABLE:
                            raise HTTPException(409,'所选 SKU 已成功、正在处理或结果未知，禁止重复提交')
                        row=all_rows[sku]
                        pt=row['product_type'];s=cache_value(db,bound,'spec',doc['version'],pt)
                        if not s or fingerprint(s)!=report['schema_digests'].get(pt):
                            raise HTTPException(409,'官方规格已过期或变化，请重新加载并校验')
                        basis_item=next(i for i in basis['items'] if i['listing_id']==row['listing_id'])
                        basis_variant=next(v for v in basis_item['variants'] if v['platform_sku']==sku)
                        if source_issues(basis_item,basis_variant,sku in price_reviews,row.get('target_stock')):
                            raise HTTPException(409,'来源售价或库存不符合要求，请刷新来源并重新校验')
                        source=json.loads(db.execute('SELECT data_json FROM current_listings WHERE id=?',(row['listing_id'],)).fetchone()[0])
                        if matches(source,entries):
                            raise HTTPException(409,'商品卖家已被拉黑，已阻止提交')
                        if platform_sku.lookup(db,bound['store_id'],basis_item['source'],basis_variant)!=sku:
                            raise HTTPException(409,'平台 SKU 与目标店铺分配记录不符')
                        claim=db.execute('SELECT * FROM walmart_listing_claims WHERE store_id=? AND platform_sku=?',(bound['store_id'],sku)).fetchone()
                        if claim and claim['batch_id']!=bid:
                            raise HTTPException(409,'该店铺 SKU 已由其他批次提交，禁止重复')
                        frozen_row = row | {'stock':row['target_stock'] if row.get('target_stock') is not None else int(basis_variant['stock'])}
                        if basis_item['source'] in ('GigaB2B', 'GigaB2B_CA', 'Saleyee'):
                            frozen_row['patrol_basis'] = dict(multiplier=basis['multiplier'], landed=basis_variant['base'])
                            if basis_item['source'] == 'Saleyee':
                                from .saleyee_patrol import quote_key
                                frozen_row['patrol_basis'].update(
                                    quote=quote_key(basis_variant.get('source_facts') or {}),
                                    namespace=db.execute('SELECT namespace FROM listings WHERE id=?',
                                                         (row['listing_id'],)).fetchone()[0])
                        chosen.append(frozen_row)
                    payload={'MPItemFeedHeader':{'businessUnit':'WALMART_US','locale':'en','version':doc['version']},
                             'MPItem':[report['payloads'][sku] for sku in data.skus]}
                    if len(encoded(payload).encode())>20*1024*1024:
                        raise HTTPException(413,'沃尔玛提交文件过大，请拆分批次')
                    results={r['platform_sku']:dict(status='unknown',message='正在提交；中断时请核对原批次，勿重复提交',errors=[]) for r in chosen}
                    at=now()
                    db.execute('INSERT INTO walmart_listing_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (aid,bid,bound['store_id'],bound['partner_id'],bound['revision'],doc['revision'],str(data.request_key),request_hash,
                         'sending','',encoded(payload),encoded(chosen),encoded(results),'',at,at))
                    for sku in data.skus:
                        db.execute('''INSERT INTO walmart_listing_claims VALUES(?,?,?,?)
                            ON CONFLICT(store_id,platform_sku) DO UPDATE SET attempt_id=excluded.attempt_id''',(bound['store_id'],sku,bid,aid))
                    event(db,bid,request,'submit_started',{'attempt_id':aid,'store_id':bound['store_id'],'partner_id':bound['partner_id'],'skus':data.skus,'payload_digest':fingerprint(payload)})
                # The sending record is now committed. Never retry this POST automatically.
                try:
                    feed_id=remote.submit(payload,aid);state='processing';message='沃尔玛已接收，等待逐项处理'
                    results={r['platform_sku']:dict(status='processing',message=message,errors=[]) for r in chosen}
                except SubmissionUnknown as exc:
                    feed_id='';state='unknown';message=str(exc)
                except WalmartError as exc:
                    feed_id='';state='rejected';message=str(exc)
                    results={r['platform_sku']:dict(status='rejected',message=message,errors=[]) for r in chosen}
                with catalog.connect() as db:
                    write_lock(db)
                    db.execute('UPDATE walmart_listing_attempts SET state=?,feed_id=?,results_json=?,message=?,updated_at=? WHERE id=?',
                        (state,feed_id,encoded(results),message,now(),aid))
                    if feed_id:
                        from .listing_result_worker import enqueue
                        enqueue(db,aid,getattr(request.state,'user',{}).get('id'))
                    event(db,bid,request,'submit_'+state,{'attempt_id':aid,'feed_id':feed_id})
                    return public_attempt(attempt(db,bid,aid))
        except (WalmartError,VaultError) as exc:
            raise HTTPException(400,str(exc)) from None

    @router.post('/attempts/{aid}/refresh')
    def refresh(bid: str,aid: str,request: Request):
        admin(request)
        return refresh_attempt(catalog,bid,aid,request)

    @router.post('/attempts/{aid}/reconcile')
    def reconcile(bid: str,aid: str,data: Reconcile,request: Request):
        admin(request)
        with catalog.connect() as db:
            a=attempt(db,bid,aid)
        if a['state'] not in ('sending','unknown') or a['feed_id']:
            raise HTTPException(409,'只有结果未知且尚未关联编号的请求可关联原沃尔玛批次')
        try:
            bound,_=context(request,bid,a['partner_id'])
            with request.app.state.walmart_publisher.session(decrypt(bound['store_id'],bound['encrypted_credentials']),a['partner_id']) as remote:
                metadata=verify_feed_owner(remote,data.feed_id,a['partner_id'])
                if metadata.get('fileName') != f'jiwu-{aid}.json':
                    raise HTTPException(409,'沃尔玛批次文件名与本次提交编号不匹配，不能将旧批次冒认为本次提交')
                response,details=read_feed(remote,data.feed_id)
                expected={r['platform_sku'] for r in json.loads(a['rows_json'])}
                if {r.get('sku') for r in details}!=expected or response.get('itemsReceived')!=len(expected):
                    raise HTTPException(409,'原批次商品清单不完整或与本次 SKU 不一致，未关联；请等待平台返回完整明细')
            with catalog.connect() as db:
                write_lock(db);current=attempt(db,bid,aid)
                if current['feed_id'] or current['state'] not in ('sending','unknown'):
                    raise HTTPException(409,'该请求已被其他操作处理，请刷新')
                duplicate=db.execute('SELECT 1 FROM walmart_listing_attempts WHERE store_id=? AND feed_id=?',(a['store_id'],data.feed_id)).fetchone()
                if duplicate:
                    raise HTTPException(409,'该沃尔玛批次编号已经关联其他提交')
                db.execute("UPDATE walmart_listing_attempts SET state='processing',feed_id=?,message=?,updated_at=? WHERE id=?",(data.feed_id,'已关联原沃尔玛批次，请查询处理结果',now(),aid))
                from .listing_result_worker import enqueue
                enqueue(db,aid,getattr(request.state,'user',{}).get('id'))
                event(db,bid,request,'reconcile',{'attempt_id':aid,'feed_id':data.feed_id})
            return refresh_attempt(catalog,bid,aid,request)
        except (WalmartError,VaultError) as exc:
            raise HTTPException(400,str(exc)) from None

    return router


def verify_feed_owner(remote, feed_id, partner_id):
    records=remote.feed_owner(feed_id).get('results',{}).get('feed',[])
    same=[r for r in records if r.get('feedId')==feed_id]
    if len(same)!=1 or str(same[0].get('partnerId'))!=partner_id or same[0].get('feedType')!='MP_ITEM':
        raise WalmartError('沃尔玛批次编号不属于该店铺的商品上架请求，已阻止关联或查询')
    return same[0]


def read_feed(remote, feed_id):
    summary=None;details=[];offset=0
    while True:
        page=remote.feed(feed_id,offset)
        if page.get('feedId')!=feed_id:
            raise WalmartError('沃尔玛返回了其他批次的处理结果，已阻止写入')
        rows=page.get('itemDetails',{}).get('itemIngestionStatus',[])
        if not isinstance(rows,list) or not all(isinstance(r,dict) for r in rows):
            raise WalmartError('沃尔玛逐项处理结果格式异常')
        summary=summary or page
        details.extend(rows)
        received=page.get('itemsReceived')
        if not isinstance(received,int) or received<0 or received>1000:
            raise WalmartError('沃尔玛批次商品数量异常，未更新状态')
        if len(details)>=received or len(rows)<1000:
            break
        offset+=len(rows)
        if offset>1000:
            raise WalmartError('沃尔玛分页结果超出批次范围')
    skus=[r.get('sku') for r in details]
    if len(skus)!=len(set(skus)):
        raise WalmartError('沃尔玛返回重复 SKU，无法确认完整性')
    return summary,details


def refresh_attempt(catalog,bid,aid,request,*,sync_inventory=True):
    token=uuid.uuid4().hex
    with catalog.connect() as db:
        write_lock(db);attempt(db,bid,aid)
        prior=db.execute('SELECT * FROM walmart_listing_polls WHERE attempt_id=?',(aid,)).fetchone()
        if prior and prior['started_at']>time.time()-180:
            raise HTTPException(409,'此提交记录正在查询，请稍后刷新')
        db.execute('''INSERT INTO walmart_listing_polls VALUES(?,?,?) ON CONFLICT(attempt_id)
            DO UPDATE SET token=excluded.token,started_at=excluded.started_at''',(aid,token,int(time.time())))
    try:
        return _refresh_attempt(catalog,bid,aid,request,token,sync_inventory=sync_inventory)
    finally:
        with catalog.connect() as db:
            write_lock(db)
            db.execute('DELETE FROM walmart_listing_polls WHERE attempt_id=? AND token=?',(aid,token))


def inventory_target(catalog,row):
    """Before a stock PUT, reread the exact SKU's current source, never another row."""
    with catalog.connect() as db:
        stored=db.execute('SELECT data_json FROM listings WHERE id=?',(row['listing_id'],)).fetchone()
        current=db.execute('SELECT data_json FROM current_listings WHERE id=?',(row['listing_id'],)).fetchone()
    if not stored or not current:
        raise WalmartError('商品来源已不存在，暂停库存写入')
    source=json.loads(stored[0]);item=json.loads(current[0])
    if row.get('target_stock') is not None:
        candidates=item.get('variants') or [item]
        if len([v for v in candidates if v['sku']==row['source_sku']])!=1:
            raise WalmartError('库存来源 SKU 不匹配，暂停写入')
        quantity=row['target_stock']
        if type(quantity) is not int or not 0<=quantity<=999999999:
            raise WalmartError('目标库存必须为 0 到 999999999 的整数')
        return quantity
    try:
        age=time.time()-datetime.fromisoformat(source['observed_at']).timestamp()
        if age>MAX_SOURCE_AGE or age < -300:
            raise ValueError()
    except (ValueError,TypeError,KeyError):
        raise WalmartError('来源库存超过 24 小时未更新，暂不自动写入；请同步来源后查询并同步库存。不影响平台发布状态') from None
    if item['source'] in ('GigaB2B', 'GigaB2B_CA', 'Saleyee'):
        if item['source'] == 'Saleyee':
            from .saleyee_prelisting import prepare
        else:
            from .giga_prelisting import prepare
        if item['sku']!=row['source_sku']:
            raise WalmartError('库存来源 SKU 不匹配，暂停写入')
        variants,_,_=prepare(item,source,Decimal('1'))
        stock=variants[0]['stock']
    else:
        candidates=item.get('variants') or [item]
        matched=[v for v in candidates if v['sku']==row['source_sku']]
        stock=matched[0]['stock'] if len(matched)==1 else None
    if str(stock) not in ('0','10'):
        raise WalmartError('该 SKU 当前库存未知或不符合 0 / 10 规则，暂停写入')
    return int(stock)


def _refresh_attempt(catalog,bid,aid,request,poll_token,*,sync_inventory=True):
    with catalog.connect() as db:
        a=attempt(db,bid,aid)
        if not a['feed_id']:
            raise HTTPException(409,'没有沃尔玛批次编号，结果未知；请在卖家中心核对并关联原批次，不能直接重提')
        _,bound=binding(db,a['store_id'],a['partner_id'])
    try:
        with request.app.state.walmart_publisher.session(decrypt(a['store_id'],bound['encrypted_credentials']),a['partner_id'],writing=sync_inventory) as remote:
            verify_feed_owner(remote,a['feed_id'],a['partner_id'])
            summary,details=read_feed(remote,a['feed_id'])
            submitted={r['platform_sku']:r for r in json.loads(a['rows_json'])}
            indexed={r['sku']:r for r in details}
            if set(indexed)-set(submitted) or summary['itemsReceived']>len(submitted):
                raise WalmartError('沃尔玛批次 SKU 与本地记录不一致，已阻止串位')
            results=json.loads(a['results_json'])
            feed_errors=upstream_reason(summary.get('ingestionErrors',{}).get('ingestionError',[]))
            for sku,row in submitted.items():
                with catalog.connect() as db:
                    write_lock(db)
                    lease=db.execute('SELECT token FROM walmart_listing_polls WHERE attempt_id=?',(aid,)).fetchone()
                    if not lease or lease['token']!=poll_token:
                        raise WalmartError('查询任务归属已变化，暂停库存写入，请重新查询')
                    db.execute('UPDATE walmart_listing_polls SET started_at=? WHERE attempt_id=?',(int(time.time()),aid))
                detail=indexed.get(sku)
                if not detail:
                    # Absence is not evidence that an item failed; never make it retryable.
                    results[sku]=dict(status='processing',message='沃尔玛尚未返回该 SKU 的完整处理结果',errors=feed_errors)
                    if summary.get('feedStatus')=='ERROR' and feed_errors:
                        results[sku]=dict(status='failed',message='沃尔玛批次处理失败，请按错误提示修正',errors=feed_errors)
                    continue
                status=detail.get('ingestionStatus')
                errors=upstream_reason(detail.get('ingestionErrors',{}).get('ingestionError',[]))
                if status in ('DATA_ERROR','SYSTEM_ERROR','TIMEOUT_ERROR'):
                    results[sku]=dict(status='failed',ingestion_status=status,message='商品处理失败，可以修改资料后重新校验、重试',errors=errors or feed_errors)
                    continue
                if status!='SUCCESS':
                    results[sku]=dict(status='processing',message='沃尔玛正在处理或进行合规审核，请稍后查询',errors=errors)
                    continue
                # Publication is independent of source freshness and inventory synchronization.
                previous=results.get(sku,{})
                stock_done=previous.get('stock_verified') is True
                try:
                    item=remote.item(sku)
                    candidate=item.get('ItemResponse',item.get('responseRecord',[]))
                    if isinstance(candidate,dict):
                        candidate=[candidate]
                    if not isinstance(candidate,list) or len(candidate)!=1 or candidate[0].get('sku')!=sku:
                        raise WalmartError('商品状态回读与目标 SKU 不一致')
                    detail_item=candidate[0];published=detail_item.get('publishedStatus')
                    if published=='PUBLISHED':
                        results[sku]=dict(status='published',message='沃尔玛已确认商品发布成功',errors=[],stock_verified=stock_done)
                    elif published=='UNPUBLISHED':
                        reasons=detail_item.get('unpublishedReasons',{}).get('reason',[])
                        results[sku]=dict(status='unpublished',message='商品资料已处理，但尚未发布；请查看平台原因',errors=upstream_reason([{'description':r} for r in reasons]),stock_verified=stock_done)
                    else:
                        results[sku]=dict(status='ingested',message='商品资料处理成功，等待平台发布',errors=[],stock_verified=stock_done)
                    results[sku].update(published_status=published or '',wpid=detail.get('wpid',''),item_id=detail.get('itemid',''))
                except WalmartError as exc:
                    results[sku]=dict(status='ingested',message='资料已接收，平台发布状态暂未核实：'+str(exc),
                                      errors=[],stock_verified=stock_done,
                                      stock_message=previous.get('stock_message',''))
                    continue
                try:
                    if not stock_done:
                        with catalog.connect() as db:
                            claim=db.execute('SELECT attempt_id FROM walmart_listing_claims WHERE store_id=? AND platform_sku=?',(a['store_id'],sku)).fetchone()
                        if not claim or claim['attempt_id']!=aid:
                            # Historical attempts stay readable but cannot overwrite a newer retry's stock.
                            results[sku]['stock_message']='此 SKU 已有较新提交，本历史记录不再写入库存'
                            continue
                        target=inventory_target(catalog,row)
                        node=row['fulfillment_center_id']
                        inventory=remote.inventory(sku,node)
                        if inventory.get('sku')!=sku:
                            raise WalmartError('库存回读 SKU 与请求不一致，暂停写入')
                        # A GET can take time; recheck source and ownership immediately
                        # before a potentially consequential inventory PUT.
                        with catalog.connect() as db:
                            write_lock(db)
                            lease=db.execute('SELECT token FROM walmart_listing_polls WHERE attempt_id=?',(aid,)).fetchone()
                            claim=db.execute('SELECT attempt_id FROM walmart_listing_claims WHERE store_id=? AND platform_sku=?',(a['store_id'],sku)).fetchone()
                            if not lease or lease['token']!=poll_token or not claim or claim['attempt_id']!=aid:
                                raise WalmartError('查询或 SKU 归属已变化，暂停库存写入')
                            db.execute('UPDATE walmart_listing_polls SET started_at=? WHERE attempt_id=?',(int(time.time()),aid))
                        target=inventory_target(catalog,row)
                        if inventory.get('quantity',{}).get('amount')!=target:
                            if not sync_inventory:
                                raise WalmartError('资料已接收，库存尚未核验；请点击查询并同步库存')
                            remote.set_inventory(sku,target,node)
                            inventory=remote.inventory(sku,node)
                        if inventory.get('sku')!=sku or inventory.get('quantity',{}).get('amount')!=target:
                            raise WalmartError('库存写入后的回读与该 SKU 目标库存不一致，请再次同步结果')
                        stock_done=True
                    results[sku].update(stock_verified=stock_done,stock_message='库存回读已通过')
                except WalmartError as exc:
                    results[sku].update(stock_verified=stock_done,stock_message=str(exc))
            terminal=all(r.get('status') in ('failed','published','unpublished') for r in results.values())
            state='complete' if terminal else 'processing'
        with catalog.connect() as db:
            write_lock(db)
            # Only update the same bound identity; never merge a response into another attempt.
            _,current=binding(db,a['store_id'],a['partner_id'])
            if current['revision']!=bound['revision']:
                raise HTTPException(409,'查询期间店铺绑定变化，请重新查询')
            db.execute('UPDATE walmart_listing_attempts SET state=?,results_json=?,message=?,updated_at=? WHERE id=?',
                (state,encoded(results),'处理结果已更新',now(),aid))
            event(db,bid,request,'refresh_result',{'attempt_id':aid,'statuses':{s:r['status'] for s,r in results.items()}})
            return public_attempt(attempt(db,bid,aid))
    except (WalmartError,VaultError) as exc:
        raise HTTPException(400,str(exc)) from None


def public_media_routes(catalog,storage):
    router=APIRouter()

    @router.get('/api/listing-media/{digest}/{signature}')
    def media(digest: str,signature: str):
        if not re.fullmatch('[a-f0-9]{64}',digest) or not re.fullmatch('[a-f0-9]{64}',signature):
            raise HTTPException(404)
        try:
            if not hmac.compare_digest(signature,image_signature(digest)):
                raise HTTPException(404)
            # Only publish images explicitly referenced by a validated, submitted payload.
            with catalog.connect() as db:
                issued=db.execute('SELECT 1 FROM walmart_listing_attempts WHERE payload_json LIKE ? LIMIT 1',('%/listing-media/'+digest+'/'+signature+'%',)).fetchone()
                row=db.execute('SELECT * FROM image_objects WHERE digest=?',(digest,)).fetchone()
                local=None if catalog.postgres else db.execute('SELECT content,mime FROM image_assets WHERE digest=?',(digest,)).fetchone()
            if not issued:
                raise HTTPException(404)
            if row and storage:
                content=storage.get(row);mime=row['mime']
            elif local:
                content=local['content'];mime=local['mime']
                if hashlib.sha256(content).hexdigest()!=digest:
                    raise HTTPException(503,'图片校验失败')
            else:
                raise HTTPException(404)
            return Response(content,media_type=mime,headers={'Cache-Control':'public, max-age=86400'})
        except (VaultError,StorageError):
            raise HTTPException(503,'图片暂不可用') from None

    return router
