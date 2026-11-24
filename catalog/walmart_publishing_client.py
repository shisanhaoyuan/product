"""Market-scoped item feeds. POST requests are deliberately never retried here."""
import hashlib
import hmac
import json
import uuid
from contextlib import contextmanager
from urllib.parse import quote

import httpx

from .walmart_client import BASE_URL, WalmartError


class SubmissionUnknown(WalmartError):
    """Walmart may have accepted the feed; a second POST is unsafe."""


class WalmartPublishingClient:
    def __init__(self, transport=None):
        self.transport = transport

    @contextmanager
    def session(self, credentials, partner_id, *, writing=False, marketplace='US', specifications=None):
        if marketplace not in ('US', 'CA'):
            raise WalmartError('不支持的沃尔玛站点')
        market_headers = {'WM_MARKET': marketplace.lower()}
        if marketplace == 'CA':
            market_headers['WM_GLOBAL_VERSION'] = '3.1'
            identity = 'oauth:' + hashlib.sha256(credentials['client_id'].encode()).hexdigest()
            if not hmac.compare_digest(identity, partner_id):
                raise WalmartError('加拿大店铺 OAuth 身份与批次绑定不一致，已阻止操作')
        with httpx.Client(base_url=BASE_URL, timeout=httpx.Timeout(60, connect=10),
                          headers=market_headers, transport=self.transport, follow_redirects=False, trust_env=False) as client:
            result = self.call(client, 'POST', '/v3/token', auth=(credentials['client_id'], credentials['client_secret']),
                               data={'grant_type': 'client_credentials'})
            token = result.get('access_token')
            if not isinstance(token, str) or not token or len(token) > 20000 or not token.isascii() or any(c.isspace() for c in token):
                raise WalmartError('沃尔玛访问令牌异常')
            client.headers['WM_SEC.ACCESS_TOKEN'] = token
            if marketplace == 'US':
                profile = self.call(client, 'GET', '/v3/settings/partnerprofile')
                partner = profile.get('partner')
                if not isinstance(partner, dict) or str(partner.get('partnerId') or '') != partner_id:
                    raise WalmartError('沃尔玛返回的店铺身份与批次不一致，已阻止操作')
            if writing or marketplace == 'CA':
                details = self.call(client, 'GET', '/v3/token/detail')
                scopes = details.get('scopes', {})
                if details.get('is_valid') is not True or details.get('is_channel_match') is False:
                    raise WalmartError('沃尔玛令牌无效或渠道不匹配，请重新验证该店铺')
                if writing and (not isinstance(scopes, dict) or scopes.get('item') != 'full_access' or scopes.get('inventory') != 'full_access'):
                    raise WalmartError('正式上架需要商品（item）及库存（inventory）的完整写入权限，请更新店铺授权')
            yield WalmartSession(self, client, marketplace, specifications)

    @staticmethod
    def call(client, method, path, *, feed_post=False, allow_list=False, **kwargs):
        headers = {'Accept': 'application/json', 'WM_SVC.NAME': 'Walmart Marketplace',
                   'WM_QOS.CORRELATION_ID': str(uuid.uuid4()), **kwargs.pop('headers', {})}
        try:
            with client.stream(method, path, headers=headers, **kwargs) as response:
                code = response.status_code
                if code not in (200, 201, 202):
                    if feed_post and (code >= 500 or code in (408, 409) or 300 <= code < 400):
                        raise SubmissionUnknown('提交响应异常，沃尔玛可能已接收；禁止直接重提，请核对并关联原批次编号')
                    if code in (401, 403):
                        raise WalmartError('沃尔玛授权失效或权限不足，请检查该店铺的绑定')
                    if code == 429:
                        raise WalmartError('沃尔玛请求频率受限，请稍后重试')
                    raise WalmartError(f'沃尔玛请求被拒绝（HTTP {code}），请检查资料及授权后重试')
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 20 * 1024 * 1024:
                        raise ValueError()
                result = json.loads(raw)
                if not isinstance(result, dict) and not (allow_list and isinstance(result,list)):
                    raise ValueError()
                return result
        except (SubmissionUnknown, WalmartError):
            raise
        except (httpx.HTTPError, ValueError):
            if feed_post:
                raise SubmissionUnknown('提交连接中断或响应无法解析，结果未知；请关联原沃尔玛批次，不能直接重提') from None
            raise WalmartError('连接沃尔玛失败或响应异常，请稍后查询') from None


class WalmartSession:
    def __init__(self, gateway, client, marketplace='US', specifications=None):
        self.gateway, self.client = gateway, client
        self.marketplace, self.specifications = marketplace, specifications
        self.feed_type = 'MP_ITEM_INTL' if marketplace == 'CA' else 'MP_ITEM'
        self.feed_page_size = 50 if marketplace == 'CA' else 1000

    def call(self, method, path, **kwargs):
        return self.gateway.call(self.client, method, path, **kwargs)

    def taxonomy(self):
        if self.marketplace == 'CA':
            from .walmart_canada import taxonomy
            return taxonomy(self.specifications)
        return self.call('GET', '/v3/items/taxonomy', params={'version': '5.0', 'feedType': 'MP_ITEM'})

    def spec(self, product_type, version):
        if self.marketplace == 'CA':
            schema = (self.specifications or {}).get('specs', {}).get(product_type)
            if not schema or version != self.specifications['version']:
                raise WalmartError('尚未导入该加拿大商品类型或版本的官方规格，请先导入正确规格')
            return schema
        return self.call('POST', '/v3/items/spec', json={
            'feedType': 'MP_ITEM', 'version': version, 'productTypes': [product_type]})

    def exemptions(self, product_type, brand, submission_id):
        if self.marketplace == 'CA':
            raise WalmartError('加拿大暂不支持自动核验 GTIN 豁免，请使用真实 EAN／UPC／GTIN 编码')
        # Filters support partial matches; callers must check every identity exactly.
        return self.call('GET', '/v3/items/gtin-exemption/status', params={
            'productType': product_type, 'brand': brand, 'submissionId': submission_id,
            'exemptionStatus': 'APPROVED', 'limit': 1000})

    def nodes(self):
        if self.marketplace == 'CA':
            from .walmart_canada import DEFAULT_NODE
            # Global Inventory permits omitting shipNode for the seller's default
            # inventory. This is an internal selector, never a fabricated node ID.
            return [{'shipNode': DEFAULT_NODE, 'shipNodeName': '加拿大站默认库存', 'status': 'ACTIVE'}]
        return self.call('GET', '/v3/settings/shipping/shipnodes', allow_list=True)

    def submit(self, payload, attempt_id):
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        result = self.call('POST', '/v3/feeds', feed_post=True, params={'feedType': self.feed_type},
                           files={'file': (f'jiwu-{attempt_id}.json', raw, 'application/json')})
        feed_id = result.get('feedId')
        if not isinstance(feed_id, str) or not 1 <= len(feed_id) <= 300 or any(ord(c) < 33 or ord(c) > 126 for c in feed_id):
            raise SubmissionUnknown('沃尔玛未返回有效批次编号，结果未知；请在卖家中心核对后关联，不能直接重提')
        return feed_id

    def feed(self, feed_id, offset=0):
        return self.call('GET', '/v3/feeds/' + quote(feed_id, safe=''),
                         params={'includeDetails': 'true', 'offset': offset, 'limit': self.feed_page_size})

    def feed_owner(self, feed_id):
        return self.call('GET', '/v3/feeds', params={'feedId': feed_id, 'limit': 50})

    def items(self, cursor, sku=''):
        params = {'sku': sku} if sku else {'nextCursor': cursor, 'limit': 50}
        return self.call('GET', '/v3/items', params=params)

    def retire(self, sku):
        return self.call('DELETE', '/v3/items/' + quote(sku, safe=''))

    def item(self, sku):
        return self.call('GET', '/v3/items/' + quote(sku, safe=''), params={'productIdType': 'SKU'})

    def catalog_search(self, gtin):
        return self.call('GET', '/v3/items/walmart/search',
                         params={'gtin': gtin, 'responseFormat': 'DEFAULT'}, timeout=15)

    def require_scope(self, scope):
        details = self.call('GET', '/v3/token/detail')
        scopes = details.get('scopes')
        if details.get('is_valid') is not True or not isinstance(scopes, dict) or scopes.get(scope) != 'full_access':
            label = {'price': '价格', 'inventory': '库存', 'item': '商品'}[scope]
            raise WalmartError(f'店铺缺少{label}（{scope}）完整写入权限，请更新授权')

    def set_price(self, sku, value):
        # Official single-item price API; no promotions, new feed or SKU changes.
        return self.call('PUT', '/v3/price', json={'sku': sku, 'pricing': [{
            'currentPriceType': 'BASE', 'currentPrice': {'currency': 'CAD' if self.marketplace == 'CA' else 'USD', 'amount': float(value)}}]})

    def set_inventory(self, sku, quantity, node):
        return self.call('PUT', '/v3/inventory', params=self.inventory_params(sku, node),
                         json={'sku': sku, 'quantity': {'unit': 'EACH', 'amount': quantity}})

    def inventory(self, sku, node):
        return self.call('GET', '/v3/inventory', params=self.inventory_params(sku, node))

    def inventory_params(self, sku, node):
        from .walmart_canada import DEFAULT_NODE
        if self.marketplace == 'CA':
            if node != DEFAULT_NODE:
                raise WalmartError('加拿大刊登当前仅支持默认库存，请重新选择加拿大站默认库存')
            return {'sku': sku}
        return {'sku': sku, 'shipNode': node}
