"""US Marketplace item feeds. POST requests are deliberately never retried here."""
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
    def session(self, credentials, partner_id, *, writing=False):
        with httpx.Client(base_url=BASE_URL, timeout=httpx.Timeout(60, connect=10),
                          transport=self.transport, follow_redirects=False, trust_env=False) as client:
            result = self.call(client, 'POST', '/v3/token', auth=(credentials['client_id'], credentials['client_secret']),
                               data={'grant_type': 'client_credentials'})
            token = result.get('access_token')
            if not isinstance(token, str) or not token or len(token) > 20000 or not token.isascii() or any(c.isspace() for c in token):
                raise WalmartError('沃尔玛访问令牌异常')
            client.headers['WM_SEC.ACCESS_TOKEN'] = token
            profile = self.call(client, 'GET', '/v3/settings/partnerprofile')
            partner = profile.get('partner')
            if not isinstance(partner, dict) or str(partner.get('partnerId') or '') != partner_id:
                raise WalmartError('沃尔玛返回的店铺身份与批次不一致，已阻止操作')
            if writing:
                details = self.call(client, 'GET', '/v3/token/detail')
                scopes = details.get('scopes', {})
                if details.get('is_valid') is not True or not isinstance(scopes, dict) or scopes.get('item') != 'full_access' or scopes.get('inventory') != 'full_access':
                    raise WalmartError('正式上架需要商品（item）及库存（inventory）的完整写入权限，请更新店铺授权')
            yield WalmartSession(self, client)

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
    def __init__(self, gateway, client):
        self.gateway, self.client = gateway, client

    def call(self, method, path, **kwargs):
        return self.gateway.call(self.client, method, path, **kwargs)

    def taxonomy(self):
        return self.call('GET', '/v3/items/taxonomy', params={'version': '5.0', 'feedType': 'MP_ITEM'})

    def spec(self, product_type, version):
        return self.call('POST', '/v3/items/spec', json={
            'feedType': 'MP_ITEM', 'version': version, 'productTypes': [product_type]})

    def exemptions(self, product_type, brand, submission_id):
        # Filters support partial matches; callers must check every identity exactly.
        return self.call('GET', '/v3/items/gtin-exemption/status', params={
            'productType': product_type, 'brand': brand, 'submissionId': submission_id,
            'exemptionStatus': 'APPROVED', 'limit': 1000})

    def nodes(self):
        return self.call('GET', '/v3/settings/shipping/shipnodes', allow_list=True)

    def submit(self, payload, attempt_id):
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        result = self.call('POST', '/v3/feeds', feed_post=True, params={'feedType': 'MP_ITEM'},
                           files={'file': (f'jiwu-{attempt_id}.json', raw, 'application/json')})
        feed_id = result.get('feedId')
        if not isinstance(feed_id, str) or not 1 <= len(feed_id) <= 300 or any(ord(c) < 33 or ord(c) > 126 for c in feed_id):
            raise SubmissionUnknown('沃尔玛未返回有效批次编号，结果未知；请在卖家中心核对后关联，不能直接重提')
        return feed_id

    def feed(self, feed_id, offset=0):
        return self.call('GET', '/v3/feeds/' + quote(feed_id, safe=''),
                         params={'includeDetails': 'true', 'offset': offset, 'limit': 1000})

    def feed_owner(self, feed_id):
        return self.call('GET', '/v3/feeds', params={'feedId': feed_id, 'limit': 50})

    def item(self, sku):
        return self.call('GET', '/v3/items/' + quote(sku, safe=''), params={'productIdType': 'SKU'})

    def set_inventory(self, sku, quantity, node):
        return self.call('PUT', '/v3/inventory', params={'sku': sku, 'shipNode':node},
                         json={'sku': sku, 'quantity': {'unit': 'EACH', 'amount': quantity}})

    def inventory(self, sku, node):
        return self.call('GET', '/v3/inventory', params={'sku': sku, 'shipNode':node})
