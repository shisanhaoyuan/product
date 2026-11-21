export const productSources = [
  {
    source: 'Amazon',
    path: '/products',
    detail: '/product/',
    name: '亚马逊商品库',
    label: '亚马逊',
    icon: 'LayoutGrid',
  },
  {
    source: 'GigaB2B',
    path: '/giga-products',
    detail: '/giga-product/',
    name: '大建云仓商品库 US',
    label: '大建云仓 US',
    icon: 'Package',
  },
  {
    source: 'GigaB2B_CA',
    path: '/giga-canada-products',
    detail: '/giga-canada-product/',
    name: '大建云仓商品库 Canada',
    label: '大建云仓 Canada',
    icon: 'Package',
  },
  {
    source: 'Saleyee',
    path: '/saleyee-products',
    detail: '/saleyee-product/',
    name: '赛盈商品库',
    label: '赛盈',
    icon: 'Package',
  },
];
export const sourceInfo = (source?: string) =>
  productSources.find((s) => s.source === source) || productSources[0]!;
export const sourceFromPath = (path: string) =>
  productSources.find((s) => path === s.path || path.startsWith(s.detail)) || productSources[0]!;
export const isSupplier = (source?: string) =>
  ['GigaB2B', 'GigaB2B_CA', 'Saleyee'].includes(source || '');
