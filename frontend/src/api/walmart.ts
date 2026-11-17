import { request } from './client';
export interface WalmartProfile {
  partnerId: string;
  partnerName: string;
  partnerDisplayName: string;
  partnerStoreId: string;
  accountStatus: string;
}
export interface WalmartAccount {
  store_id: number;
  marketplace: 'US';
  client_hint: string;
  status: 'verified' | 'error' | 'unbound';
  revision: number;
  last_verified_at: string;
  last_attempt_at: string;
  last_error: string;
  updated_at: string;
  profile: WalmartProfile;
  scopes: Record<string, string>;
  bound: boolean;
}
export interface WalmartStore {
  default_brand?: string;
  store_id: number;
  name: string;
  active: boolean;
  sku_code: string | null;
  account: WalmartAccount | null;
}
export interface WalmartConnections {
  vault: { ready: boolean; key_id: string; message: string };
  stores: WalmartStore[];
  execution_enabled: boolean;
}
export interface WalmartVerification {
  verification_id: string;
  store_id: number;
  expires_at: number;
  profile: WalmartProfile;
  scopes: Record<string, string>;
}
const post = (data: unknown) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(data),
});
export const walmartApi = {
  accounts: () => request<WalmartConnections>('/api/walmart/accounts'),
  preview: (store_id: number, client_id: string, client_secret: string) =>
    request<WalmartVerification>(
      '/api/walmart/accounts/preview',
      post({ store_id, client_id, client_secret }),
    ),
  bind: (store_id: number, verification_id: string) =>
    request<WalmartAccount>(`/api/walmart/accounts/${store_id}/bind`, post({ verification_id })),
  verify: (store_id: number, revision: number) =>
    request<WalmartAccount>(`/api/walmart/accounts/${store_id}/verify`, post({ revision })),
  unbind: (store_id: number, revision: number) =>
    request<WalmartAccount>(`/api/walmart/accounts/${store_id}/unbind`, post({ revision })),
};
export const walmartScopeLabels: Record<string, string> = {
  item: '商品',
  items: '商品',
  feeds: '批量任务',
  price: '价格',
  inventory: '库存',
  content: '内容',
  orders: '订单',
  reports: '报表',
  lagtime: '备货时间',
  returns: '退货',
  settings: '设置',
};
export const walmartAccessLabels: Record<string, string> = {
  full_access: '读写',
  view_only: '只读',
  no_access: '无权限',
};
