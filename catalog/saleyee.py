"""Saleyee Open API V2: read-only supplier import, keyed by account and SKU.

Contract: https://api.saleyee.com/swagger/docs/v2 (2026-11-19).
Only product read endpoints are allowed; never create or pay supplier orders.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .giga import sync_lease
from .models import ImportBundle, Listing

SOURCE = 'Saleyee'
HOST = 'https://api.saleyee.com'
PATHS = {name: '/api/v2/Product/' + path for name, path in {
    'detail': 'QueryProductDetail', 'price': 'QueryProductPrice',
    'stock': 'QueryProductInventory', 'warehouses': 'GetWarehouse',
}.items()}


class SaleyeeError(ValueError):
    pass


class SaleyeeSettings:
    def __init__(self):
        self.token = os.environ.get('CATALOG_SALEYEE_ACCESS_TOKEN', '').strip()
        self.account = os.environ.get('CATALOG_SALEYEE_ACCOUNT', '').strip()
        self.configured = bool(self.token and self.account)
        # Stable account label, not the rotating token, is the import identity.
        self.namespace = 'saleyee.' + hashlib.sha256(self.account.encode()).hexdigest()[:20]

    def public(self):
        return dict(configured=self.configured, account=self.account, site='US', page_size=30)


class SaleyeeClient:
    def __init__(self, settings, transport=None):
        if not settings.configured:
            raise SaleyeeError('请配置赛盈 AccessToken 和固定账号标识')
        self.settings, self.transport = settings, transport
        self.deadline = time.monotonic() + 120
        self.last_request = 0.0

    def query(self, operation, body):
        path = PATHS[operation]
        for attempt in range(2):
            if time.monotonic() > self.deadline:
                raise SaleyeeError('赛盈本页查询超时，本页未保存')
            time.sleep(max(0, 1.1 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                with httpx.Client(timeout=15, follow_redirects=False, trust_env=False,
                                  transport=self.transport) as client:
                    with client.stream('POST', HOST + path, json=body,
                                       headers={'Authorization': 'Bearer ' + self.settings.token}) as response:
                        if response.status_code == 429 or response.status_code >= 500:
                            try:
                                delay = max(1, float(response.headers.get('Retry-After', '2')))
                            except ValueError:
                                delay = 2
                            if attempt == 0 and delay <= 20 and time.monotonic() + delay < self.deadline:
                                time.sleep(delay)
                                continue
                            raise SaleyeeError('赛盈接口限流或暂不可用，请稍后重试')
                        if response.status_code != 200:
                            raise SaleyeeError(f'赛盈 {operation} 查询失败（HTTP {response.status_code}），请检查权限')
                        raw = bytearray()
                        for chunk in response.iter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 16 * 1024 * 1024 or time.monotonic() > self.deadline:
                                raise SaleyeeError('赛盈响应过大或超时，本页未保存')
                data = json.loads(raw, parse_float=str)
            except httpx.HTTPError:
                if attempt == 0:
                    continue
                raise SaleyeeError('赛盈连接失败，请稍后重试') from None
            except (ValueError, UnicodeError) as exc:
                if isinstance(exc, SaleyeeError):
                    raise
                raise SaleyeeError('赛盈响应不是有效 JSON，本页未保存') from None
            if not isinstance(data, dict) or data.get('status') != 'success':
                # Never echo remote messages, which may contain credentials or request data.
                raise SaleyeeError('赛盈未返回成功结果，请检查 AccessToken、接口权限或稍后重试')
            return data.get('data')
        raise SaleyeeError('赛盈暂不可用')


class SyncInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    page: int = Field(default=1, ge=1, le=10000)
    skus: list[str] = Field(default_factory=list, max_length=30)
    end_date: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime('%Y-%m-%d'))
    warehouse: str = Field(default='', max_length=100)
    logistics: str = Field(default='', max_length=100)
    quote_site: str = Field(default="", max_length=200)
    shipping: Decimal | None = Field(default=None, ge=0, le=100000, max_digits=10, decimal_places=2)

    @field_validator('skus')
    @classmethod
    def valid_skus(cls, values):
        cleaned = list(dict.fromkeys(v.strip() for v in values))
        if any(not v or len(v) > 200 for v in cleaned):
            raise ValueError('SKU 不能为空或超过 200 字符')
        return cleaned

    @field_validator('end_date')
    @classmethod
    def valid_date(cls, value):
        datetime.strptime(value, '%Y-%m-%d')
        return value


class QuoteInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    warehouse: str = Field(default='', max_length=100)
    logistics: str = Field(default='', max_length=100)
    quote_site: str = Field(default='', max_length=200)
    shipping: Decimal | None = Field(default=None, ge=0, le=100000, max_digits=10, decimal_places=2)


def amount(value):
    if value in (None, ''):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError()
        return format(number, 'f')
    except (InvalidOperation, ValueError):
        raise SaleyeeError('赛盈金额格式异常，本页未保存') from None


def index_rows(rows, expected=None):
    if not isinstance(rows, list):
        raise SaleyeeError('赛盈返回的 SKU 列表格式不正确')
    result = {}
    for row in rows:
        sku = row.get('Sku') if isinstance(row, dict) else None
        if not isinstance(sku, str) or not sku or sku in result or (expected is not None and sku not in expected):
            raise SaleyeeError('赛盈响应存在重复或不匹配的 SKU，本页未保存')
        result[sku] = row
    if expected is not None and set(result) != set(expected):
        raise SaleyeeError('赛盈价格或库存缺少部分 SKU，本页未保存，已有数据保留')
    return result


def summarize(detail, price, stock, options):
    warnings = []
    offers = []
    for entry in price.get('WarehousePriceList') or []:
        if not isinstance(entry, dict):
            raise SaleyeeError('赛盈仓库价格格式异常')
        currency = str(entry.get('CurrencyCode') or '').upper()
        # Do not infer country from warehouse names; select only explicit US quotes.
        if currency != 'USD' or str(entry.get('ReceiveCountryCode') or '').upper() != 'US':
            continue
        if options.quote_site and entry.get('Store') != options.quote_site:
            continue
        if options.warehouse and entry.get('StockCode') != options.warehouse:
            continue
        if options.logistics and entry.get('LogisticsProductCode') != options.logistics:
            continue
        offers.append(entry)
    selected = offers[0] if len(offers) == 1 else None
    original = amount(selected.get('OriginalPrice')) if selected else None
    selling = amount(selected.get('SellingPrice')) if selected else None
    base = original if original is not None and Decimal(original) > 0 else selling
    inventories = []
    for site in stock.get('ProductInventorySiteList') or []:
        for region in site.get('ProductInventoryList') or []:
            inventories.append(dict(site=site.get('SiteHosts'), **region))
    matches = [v for v in inventories if selected and v.get('StockCode') == selected.get('StockCode')
               and v.get('site') == selected.get('Store')]
    quantity = matches[0].get('Qty') if len(matches) == 1 else None
    if quantity is not None and (type(quantity) is not int or quantity < 0):
        raise SaleyeeError('赛盈可用库存不是非负整数')
    if not selected:
        warnings.append('请选择唯一的美国仓库及物流报价；缺少美国收货国家标识的报价不能自动选用')
    if quantity is None:
        warnings.append('没有找到与报价同站点、同仓库的可用库存')
    shipping = amount(options.shipping)
    if shipping is None:
        warnings.append('接口不含运费，请填写此报价的配送运费；确认免运费时填写 0')
    return dict(price=base, selling_price=selling, original_price=original,
                currency='USD' if selected else '', inventory=quantity,
                shipping=shipping, shipping_origin='manual' if shipping is not None else 'unknown',
                landed=format(Decimal(base) + Decimal(shipping), 'f') if base is not None and shipping is not None else None,
                selected_offer=selected, offers=offers, warehouses=inventories, warnings=warnings)


def listing_from_api(sku, detail, price, stock, options, namespace, now):
    if any(v.get('Sku') != sku for v in (detail, price, stock)):
        raise SaleyeeError('赛盈商品、价格和库存 SKU 不一致')
    info = summarize(detail, price, stock, options)
    images = [x['ImageUrl'] for x in sorted(detail.get('GoodsImageList') or [],
              key=lambda x: (not x.get('IsMainImage', False), x.get('Sort') or 0)) if x.get('ImageUrl')]
    descriptions = detail.get('GoodsDescriptionList') or []
    english = [x for x in descriptions if any(l.get('LabelName') in ('英文', 'English', 'EN')
               for l in x.get('GoodsDescriptionLabelList') or [])]
    chosen = english[:1] or descriptions[:1]
    paragraphs = [p for d in chosen for p in sorted(d.get('GoodsDescriptionParagraphList') or [],
                  key=lambda p: p.get('SortNo') or 0)]
    category = ' > '.join(detail.get(f'Category{level}NameEN') or detail.get(f'Category{level}Name') or ''
                         for level in ('First', 'Second', 'Third')).strip(' >')
    attributes = {x.get('AttributeNameEn') or x.get('AttributeName') or x.get('AttributeCode') or '未命名属性': x.get('AttributeValue')
                  for x in detail.get('ProductAttributeList') or []}
    return Listing(external_id=sku, sku=sku, asin='', source=SOURCE, source_url=detail.get('Url') or '',
        store='赛盈来源库', template='SALEYEE:' + sku, brand=detail.get('BrandName') or '',
        seller='', observed_at=now, uploaded_at=now, evidence_kind='source_only',
        copy={'title': detail.get('EnName') or detail.get('CnName') or sku,
              'description': '\n'.join(p.get('GoodsDescription') or '' for p in paragraphs),
              'features': [p['GoodsDescription'] for p in paragraphs
                           if p.get('GoodsDescription') and p.get('ParagraphName') in ('特征','卖点','Features')]},
        images=images, price=info['price'], stock=None if info['inventory'] is None else str(info['inventory']),
        fields={'Saleyee category': category, 'Saleyee attributes': attributes},
        source_facts=dict(info, sku=sku, saleyee={'detail': detail, 'price': price, 'inventory': stock,
                                               'options': options.model_dump(mode='json', exclude={'page','skus','end_date'})}),
        evidence={'adapter':'saleyee-openapi-v2','namespace':namespace,
                  'note':'赛盈 API 来源快照；供应商未返回卖家名称，不将品牌当作卖家'})


def sync_page(catalog, settings, options, actor_id=None, client=None):
    with sync_lease(catalog, settings.namespace):
        client = client or SaleyeeClient(settings)
        body = {'PageIndex': options.page, 'PageSize': 30}
        if options.skus:
            body['Skus'] = options.skus
        else:
            body['EndTime'] = options.end_date
        if options.warehouse:
            body['WarehouseCode'] = options.warehouse
        response = client.query('detail', body)
        if not isinstance(response, dict):
            raise SaleyeeError('赛盈商品详情分页格式不正确')
        details = index_rows(response.get('ProductInfoList'), options.skus or None)
        pages = response.get('PageTotal')
        if options.skus:
            pages = 1
        elif (type(pages) is not int or not 0 <= pages <= 10000
              or response.get('PageIndex') != options.page):
            raise SaleyeeError('赛盈分页信息异常，本页未保存')
        if details and not options.skus and options.page > pages:
            raise SaleyeeError('赛盈分页超出总页数，本页未保存')
        if not details:
            if options.page <= pages:
                raise SaleyeeError('赛盈当前页意外为空，本页未保存')
            return dict(page=options.page,total_pages=pages,next_page=None,count=0,created=0,updated=0,unchanged=0)
        if len(details) > 100:
            raise SaleyeeError('赛盈单页超过 100 个商品，本页未保存')
        prices, stocks = {}, {}
        skus = list(details)
        # Detail may ignore requested PageSize; price and inventory allow at most 30 SKUs.
        for start in range(0, len(skus), 30):
            batch = skus[start:start+30]
            query = {'SkuList': batch}
            prices.update(index_rows(client.query('price', query), batch))
            stocks.update(index_rows(client.query('stock', dict(query, PageIndex=1, PageSize=30)), batch))
        now = datetime.now(timezone.utc).isoformat()
        listings = []
        with catalog.connect() as db:
            for sku in skus:
                old = db.execute('SELECT data_json FROM listings WHERE namespace=? AND external_id=?',
                                 (settings.namespace, sku)).fetchone()
                saved = ((json.loads(old[0]).get('source_facts') or {}).get('saleyee') or {}).get('options', {}) if old else {}
                explicit = options.model_dump(mode='json', exclude_unset=True)
                effective = SyncInput(**(saved | explicit))
                # A manual shipping estimate belongs to exactly one selected quote.
                if any(k in explicit and explicit[k] != saved.get(k, '') for k in ('warehouse','logistics','quote_site')) and 'shipping' not in explicit:
                    effective.shipping = None
                if old and effective.shipping is not None and 'shipping' not in explicit:
                    prior_offer = (json.loads(old[0]).get('source_facts') or {}).get('selected_offer') or {}
                    next_offer = summarize(details[sku], prices[sku], stocks[sku], effective)['selected_offer'] or {}
                    keys = ('Store','StockCode','LogisticsProductCode','ReceiveCountryCode','CurrencyCode')
                    if any(prior_offer.get(k) != next_offer.get(k) for k in keys):
                        effective.shipping = None
                item = listing_from_api(sku, details[sku], prices[sku], stocks[sku], effective, settings.namespace, now)
                listings.append(item)
                if old:
                    prior = json.loads(old[0]); item.uploaded_at = prior['uploaded_at']
                    candidate = item.model_dump(); candidate['observed_at'] = prior['observed_at']
                    if candidate == prior:
                        item.observed_at = prior['observed_at']
        if time.monotonic() > getattr(client, 'deadline', float('inf')):
            raise SaleyeeError('赛盈同步超时，本页未保存')
        result = catalog.ingest(ImportBundle(namespace=settings.namespace,
            label=f'赛盈 · 第 {options.page} 页', listings=listings), actor_id=actor_id)
        return result | dict(page=options.page,total_pages=pages,
                             next_page=options.page+1 if options.page < pages else None,count=len(skus))


def saleyee_routes(catalog):
    router = APIRouter(prefix='/api/integrations/saleyee')

    @router.get('')
    def status():
        return SaleyeeSettings().public()

    @router.post('/sync')
    def sync(data: SyncInput, request: Request):
        settings = SaleyeeSettings()
        if not settings.configured:
            raise HTTPException(503, '请在服务器配置赛盈 AccessToken 和固定账号标识')
        user = getattr(request.state, 'user', None)
        try:
            return sync_page(catalog, settings, data, user['id'] if user else None)
        except SaleyeeError as exc:
            raise HTTPException(502, str(exc)) from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise HTTPException(502, '赛盈响应字段格式异常，本页未保存') from None
    @router.post('/products/{product_id}/refresh')
    def refresh(product_id: str, data: QuoteInput, request: Request):
        settings = SaleyeeSettings()
        if not settings.configured:
            raise HTTPException(503, '请先配置赛盈 API')
        with catalog.connect() as db:
            record = db.execute('SELECT namespace,data_json FROM listings WHERE id=?', (product_id,)).fetchone()
        if not record:
            raise HTTPException(404, '商品不存在')
        source = json.loads(record['data_json'])
        if record['namespace'] != settings.namespace or source.get('source') != SOURCE:
            raise HTTPException(409, '此商品不属于当前配置的赛盈账号，禁止跨账号同步')
        saved = ((source.get('source_facts') or {}).get('saleyee') or {}).get('options', {})
        values = saved | data.model_dump(mode='json', exclude_unset=True)
        if any(k in data.model_fields_set and values[k] != saved.get(k, '') for k in ('warehouse','logistics','quote_site')) and 'shipping' not in data.model_fields_set:
            values['shipping'] = None
        user = getattr(request.state, 'user', None)
        try:
            sync_page(catalog, settings, SyncInput(skus=[source['sku']], **values), user['id'] if user else None)
            return catalog.get(product_id)
        except SaleyeeError as exc:
            raise HTTPException(502, str(exc)) from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise HTTPException(502, '赛盈响应字段格式异常，已有商品保留') from None
    return router
