"""GigaB2B Buyer Open API 2.0. Only the four documented read operations.

Credentials are environment-only. API responses are joined by exact item code;
no supplier ID is synthesized into an ASIN, and no listing markup is applied.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict

from .database import write_lock
from .models import ImportBundle, Listing, safe_url

PATHS = {
    'list': '/b2b-overseas-api/v1/buyer/product/skus/v1',
    'detail': '/b2b-overseas-api/v1/buyer/product/detailInfo/v1',
    'price': '/b2b-overseas-api/v1/buyer/product/price/v1',
    'stock': '/b2b-overseas-api/v1/buyer/inventory/quantity/v2',
}
HOSTS = {'production': 'https://openapi.gigab2b.com',
         'sandbox': 'https://openapi-sandbox.gigab2b.com'}


class GigaError(ValueError):
    pass


class GigaSettings:
    def __init__(self):
        self.environment = os.environ.get('CATALOG_GIGA_ENVIRONMENT', 'sandbox')
        self.client_id = os.environ.get('CATALOG_GIGA_CLIENT_ID', '').strip()
        self.secret = os.environ.get('CATALOG_GIGA_CLIENT_SECRET', '').strip()
        self.configured = bool(self.client_id and self.secret and self.environment in HOSTS)
        # Isolate accounts and sandbox data without exposing their client IDs.
        account = hashlib.sha256(self.client_id.encode()).hexdigest()[:16]
        self.namespace = f'giga.{self.environment}.{account}'

    def public(self):
        return {'configured': self.configured, 'environment': self.environment if self.environment in HOSTS else 'invalid',
                'scope': '仅同步 Buyer 收藏夹或有囤货库存的商品', 'page_size': 100}


def sign(client_id, secret, path, timestamp, nonce):
    message = f'{client_id}&{path}&{timestamp}&{nonce}'
    key = f'{client_id}&{secret}&{nonce}'
    hexadecimal = hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()
    return base64.b64encode(hexadecimal.encode()).decode()


class GigaClient:
    def __init__(self, settings, transport=None):
        if not settings.configured:
            raise GigaError('请先在服务器配置大建云仓环境及 Client ID / Secret')
        self.settings = settings
        self.transport = transport
        self.last_request = 0.0
        self.deadline = time.monotonic() + 90

    def query(self, operation, body):
        path = PATHS[operation]
        # Sequential, <= 1 request/second, including retries. Never follow redirects
        # carrying authentication headers; never include remote bodies in errors.
        for attempt in range(2):
            if time.monotonic() > self.deadline:
                raise GigaError('本页同步超时，请重试；未提交的本页不会覆盖商品')
            time.sleep(max(0, 1.1 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            timestamp = str(int(time.time() * 1000))
            nonce = ''.join(secrets.choice('0123456789') for _ in range(10))
            headers = {'Content-Type': 'application/json', 'client-id': self.settings.client_id,
                       'timestamp': timestamp, 'nonce': nonce,
                       'sign': sign(self.settings.client_id, self.settings.secret, path, timestamp, nonce)}
            try:
                with httpx.Client(timeout=10, follow_redirects=False, trust_env=False,
                                  transport=self.transport) as client:
                    with client.stream('POST', HOSTS[self.settings.environment] + path,
                                       headers=headers, json=body) as response:
                        if response.status_code == 429 or response.status_code >= 500:
                            if attempt == 0:
                                time.sleep(2)
                                continue
                        if response.status_code != 200:
                            raise GigaError(f'大建云仓 {operation} 接口 HTTP {response.status_code}；请检查权限或稍后重试')
                        raw = bytearray()
                        for chunk in response.iter_bytes():
                            if time.monotonic() > self.deadline:
                                raise GigaError('本页查询超时，本页未保存')
                            raw.extend(chunk)
                            if len(raw) > 16 * 1024 * 1024:
                                raise GigaError('大建云仓响应过大，已停止本页同步')
                data = json.loads(raw, parse_float=str)
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 0:
                    continue
                raise GigaError('大建云仓连接超时或网络不可用，请稍后重试') from None
            except (json.JSONDecodeError, UnicodeError):
                raise GigaError('大建云仓返回非 JSON 数据，本页未保存') from None
            except httpx.HTTPError:
                raise GigaError('大建云仓连接异常，本页未保存') from None
            if not isinstance(data, dict) or data.get('success') is not True or str(data.get('code')) != '200':
                raise GigaError('大建云仓拒绝了查询，请检查 API 环境、密钥、权限及服务器时间')
            return data.get('data')
        raise GigaError('大建云仓暂时不可用')


def indexed(records, expected, label):
    if not isinstance(records, list):
        raise GigaError(f'{label}响应格式不正确，本页未保存')
    result = {}
    for row in records:
        sku = row.get('sku') if isinstance(row, dict) else None
        if not isinstance(sku, str) or not sku or sku not in expected or sku in result:
            raise GigaError(f'{label}存在重复或不匹配的 SKU，本页未保存')
        result[sku] = row
    if set(result) != set(expected):
        raise GigaError(f'{label}缺少部分 SKU，本页未保存，已有商品保持原值')
    return result


def amount(value):
    if value is None or value == '':
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise GigaError('接口金额格式异常，本页未保存') from None
    if not number.is_finite() or number < 0:
        raise GigaError('接口金额格式异常，本页未保存')
    return format(number, 'f')


def listing_from_api(sku, detail, price, stock, namespace, now):
    for row in (detail, price, stock):
        if row.get('sku') != sku:
            raise GigaError('商品 SKU 对应失败，本页未保存')
    original = amount(price.get('price'))
    shipping = amount(price.get('shippingFee'))
    quantity = (stock.get('sellerInventoryInfo') or {}).get('sellerAvailableInventory')
    if quantity is not None and (type(quantity) is not int or quantity < 0):
        raise GigaError('可售库存不是非负整数，本页未保存')
    seller = (detail.get('sellerInfo') or {}).get('sellerStore') or (price.get('sellerInfo') or {}).get('sellerStore') or ''
    images = []
    for url in [detail.get('mainImageUrl'), *(detail.get('imageUrls') or [])]:
        if url and url not in images:
            try:
                images.append(safe_url(url))
            except (ValueError, TypeError):
                raise GigaError('商品图片链接无效，本页未保存') from None
    if len(images) > 100:
        raise GigaError('单商品图片超过 100 张，本页未保存')
    brands = [b.get('brandName') for b in detail.get('brandInfo') or [] if isinstance(b, dict) and b.get('brandName')]
    facts = dict(sku=sku, price=original, shipping=shipping, currency=price.get('currency'), seller=seller,
                 inventory=quantity, inventory_basis='sellerAvailableInventory', price_basis='price',
                 giga={'detail': detail, 'price': price, 'inventory': stock})
    return Listing(external_id=sku, asin='', sku=sku, source='GigaB2B',
                   # No documented product permalink: do not invent one.
                   source_url='', store='大建云仓来源库', template=f'GIGA:{sku}',
                   brand=brands[0] if len(set(brands)) == 1 else '', seller=seller,
                   observed_at=now, uploaded_at=now, evidence_kind='source_only',
                   copy={'title': detail.get('productName') or sku,
                         'description': detail.get('description') or '',
                         'features': detail.get('characteristics') or []},
                   images=images, price=original, stock=None if quantity is None else str(quantity),
                   ean=detail.get('upc') or '', source_facts=facts,
                   fields={'GIGA category': detail.get('categoryPath') or detail.get('category') or '',
                           'GIGA attributes': detail.get('attributes') or {},
                           'GIGA SKU available': detail.get('skuAvailable')},
                   evidence={'adapter': 'giga-openapi-v2', 'namespace': namespace,
                             'note': '供应商来源资料；价格为原价，库存为 Seller 可售库存；未刊登、未经 ERP 回读'})


@contextmanager
def sync_lease(catalog, namespace):
    token = secrets.token_hex(16)
    # One database-backed lease across web workers; survives process interruption
    # until expiry. No DB transaction is held open during outbound network calls.
    with catalog.connect() as db:
        write_lock(db)
        old = db.execute('SELECT expires_at FROM integration_leases WHERE name=?', (namespace,)).fetchone()
        if old and old[0] > time.time():
            raise HTTPException(409, '此大建云仓账号正在同步，请等待本页完成后重试')
        db.execute('INSERT INTO integration_leases VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET token=excluded.token,expires_at=excluded.expires_at',
                   (namespace, token, time.time() + 180))
    try:
        yield token
    finally:
        with catalog.connect() as db:
            write_lock(db)
            db.execute('DELETE FROM integration_leases WHERE name=? AND token=?', (namespace, token))


def sync_page(catalog, settings, page, actor_id=None, client=None):
    with sync_lease(catalog, settings.namespace):
        client = client or GigaClient(settings)
        result = client.query('list', {'page': page, 'pageSize': 100, 'sort': 3})
        if not isinstance(result, dict) or not isinstance(result.get('pageInfo'), dict):
            raise GigaError('商品列表缺少分页信息，本页未保存')
        info = result['pageInfo']
        pages = info.get('totalPage')
        if type(pages) is not int or pages < 0 or pages > 10000 or info.get('page') != page:
            raise GigaError('商品列表分页信息异常，本页未保存')
        records = result.get('records')
        if not isinstance(records, list) or len(records) > 100:
            raise GigaError('商品列表格式异常，本页未保存')
        skus = [r.get('sku') if isinstance(r, dict) else None for r in records]
        indexed(records, skus, '商品列表')
        if not skus:
            if pages > 0 and page <= pages:
                raise GigaError('商品列表意外为空，请重新同步')
            return dict(page=page, total_pages=pages, next_page=None, created=0, updated=0, unchanged=0, count=0)
        if page > pages:
            raise GigaError('商品列表页码超出范围，本页未保存')
        detail = indexed(client.query('detail', {'skus': skus}), skus, '商品详情')
        prices = indexed(client.query('price', {'skus': skus}), skus, '价格')
        stocks = indexed(client.query('stock', {'skus': skus}), skus, '库存')
        now = datetime.now(timezone.utc).isoformat()
        listings = [listing_from_api(s, detail[s], prices[s], stocks[s], settings.namespace, now) for s in skus]
        with catalog.connect() as db:
            for item in listings:
                old = db.execute('SELECT data_json FROM listings WHERE namespace=? AND external_id=?', (settings.namespace, item.external_id)).fetchone()
                if old:
                    prior = json.loads(old[0])
                    item.uploaded_at = prior['uploaded_at']
                    candidate = item.model_dump()
                    candidate['observed_at'] = prior['observed_at']
                    if candidate == prior:
                        item.observed_at = prior['observed_at']
        # All four responses validated before a single transactional page import.
        if time.monotonic() > getattr(client, 'deadline', float('inf')):
            raise GigaError('本页处理超时，请重试')
        imported = catalog.ingest(ImportBundle(namespace=settings.namespace,
            label=f'大建云仓 {settings.environment} · 第 {page} 页', listings=listings), actor_id=actor_id)
        return imported | dict(page=page, total_pages=pages, next_page=page + 1 if page < pages else None, count=len(skus))


class SyncPageInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    page: int = Field(default=1, ge=1, le=10000)


def giga_routes(catalog):
    router = APIRouter(prefix='/api/integrations/giga')

    @router.get('')
    def status():
        return GigaSettings().public()

    @router.post('/sync')
    def sync(data: SyncPageInput, request: Request):
        settings = GigaSettings()
        if not settings.configured:
            raise HTTPException(503, '尚未配置大建云仓 API，请由管理员配置服务器环境变量')
        user = getattr(request.state, 'user', None)
        try:
            return sync_page(catalog, settings, data.page, user['id'] if user else None)
        except GigaError as exc:
            raise HTTPException(502, str(exc)) from None
        except (ValueError, TypeError, KeyError):
            raise HTTPException(502, '大建云仓数据格式异常或身份冲突，本页未保存') from None

    return router
