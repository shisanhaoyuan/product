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
    name: '大建云仓商品库',
    label: '大建云仓',
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
export const isSupplier = (source?: string) => ['GigaB2B', 'Saleyee'].includes(source || '');
