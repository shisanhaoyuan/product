"""Read-only US / Canada OAuth verification. No feeds or publish endpoint."""
import uuid
import math

import httpx

BASE_URL = 'https://marketplace.walmartapis.com'
SCOPES = ('item', 'items', 'feeds', 'price', 'inventory', 'content', 'orders', 'reports',
          'lagtime', 'returns', 'settings')


class WalmartError(Exception):
    pass


class WalmartVerifier:
    def __init__(self, transport=None):
        self.transport = transport

    def verify(self, client_id, client_secret, marketplace='US'):
        if marketplace not in ('US', 'CA'):
            raise WalmartError('请选择美国 US 或加拿大 CA 站点')
        with httpx.Client(base_url=BASE_URL, timeout=httpx.Timeout(20, connect=10),
                          headers={'WM_MARKET': marketplace.lower(), **({'WM_GLOBAL_VERSION':'3.1'} if marketplace=='CA' else {})},
                          follow_redirects=False, trust_env=False, transport=self.transport) as client:
            token_data = self.call(client, 'POST', '/v3/token', auth=(client_id, client_secret),
                                   data={'grant_type': 'client_credentials'})
            token = token_data.get('access_token')
            if not isinstance(token, str) or not token or len(token) > 20000 or not token.isascii() or any(c.isspace() for c in token):
                raise WalmartError('沃尔玛未返回有效访问令牌，请检查 API 授权')
            try:
                expiry = float(token_data['expires_in'])
                if not math.isfinite(expiry) or expiry <= 0:
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                raise WalmartError('沃尔玛返回的令牌有效期异常，请稍后重新验证') from None
            headers = {'WM_SEC.ACCESS_TOKEN': token}
            details = self.call(client, 'GET', '/v3/token/detail', headers=headers)
            if details.get('is_valid') is not True:
                raise WalmartError('沃尔玛返回令牌无效，请检查 API 授权')
            if details.get('is_channel_match') is False:
                raise WalmartError('沃尔玛返回授权渠道不匹配，请检查密钥来源')
            raw_scopes = details.get('scopes')
            scopes = {k: v for k, v in (raw_scopes.items() if isinstance(raw_scopes, dict) else [])
                      if k in SCOPES and v in ('full_access', 'view_only', 'no_access')}
            if marketplace == 'CA':
                # Global partnerprofile is documented US-only. Token detail does
                # not document seller identity; do not invent it or decode tokens.
                return {'profile': dict(partnerId='', partnerName='', partnerDisplayName='',
                                        partnerStoreId='', accountStatus='', identityVerified=False),
                        'scopes': scopes}
            raw_profile = self.call(client, 'GET', '/v3/settings/partnerprofile', headers=headers)
        partner = raw_profile.get('partner')
        if not isinstance(partner, dict):
            raise WalmartError('未能读取沃尔玛店铺身份，请确认 API 权限后重试')
        profile = {}
        # Do not persist tax/business registration IDs or arbitrary response fields.
        for field in ('partnerId', 'partnerName', 'partnerDisplayName', 'partnerStoreId'):
            value = partner.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int))):
                raise WalmartError('沃尔玛店铺身份响应格式异常')
            value = str(value or '').strip()
            if len(value) > 300 or any(secret and secret in value for secret in (client_id, client_secret, token)):
                raise WalmartError('沃尔玛店铺身份响应格式异常')
            profile[field] = value
        if not profile['partnerId'] or not (profile['partnerDisplayName'] or profile['partnerName']):
            raise WalmartError('沃尔玛未返回完整店铺编号和名称，未进行绑定')
        profile['accountStatus'] = ''
        for entry in raw_profile.get('configurations', []) or []:
            if isinstance(entry, dict) and entry.get('configurationName') == 'ACCOUNT':
                config = entry.get('configuration')
                status = config.get('status') if isinstance(config, dict) else None
                if status in ('ACTIVE', 'INACTIVE', 'SUSPENDED', 'TERMINATED', 'ONBOARDING'):
                    profile['accountStatus'] = status
        return {'profile': profile, 'scopes': scopes}

    @staticmethod
    def call(client, method, path, **kwargs):
        headers = {'Accept': 'application/json', 'WM_QOS.CORRELATION_ID': str(uuid.uuid4()),
                   'WM_SVC.NAME': 'Walmart Marketplace', **kwargs.pop('headers', {})}
        try:
            with client.stream(method, path, headers=headers, **kwargs) as response:
                if response.status_code in (401, 403):
                    raise WalmartError('密钥无效或 API 权限不足，请检查所选站点、正式环境密钥和授权')
                if response.status_code == 429:
                    raise WalmartError('沃尔玛请求频率受限，请稍后重新验证')
                if response.status_code != 200:
                    raise WalmartError(f'沃尔玛验证请求失败（HTTP {response.status_code}），请检查站点与授权后重试')
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 1024 * 1024:
                        raise WalmartError('沃尔玛验证响应超出限制')
                import json
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError()
                return result
        except WalmartError:
            raise
        except httpx.HTTPError:
            # Never expose upstream response, request headers, credentials or token.
            raise WalmartError('连接沃尔玛失败或超时，请检查服务器网络后重试') from None
        except ValueError:
            raise WalmartError('沃尔玛返回的响应无法解析，请核对站点后重试') from None
