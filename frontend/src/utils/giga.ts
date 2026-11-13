export function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
export function array(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(object) : [];
}
export function level(value: unknown): string {
  const text = String(value ?? '').trim();
  return (
    (
      {
        low: '低',
        high: '高',
        moderate: '中',
        medium: '中',
        'n/a': '未提供',
        yes: '是',
        no: '否',
        'not applicable': '不适用',
      } as Record<string, string>
    )[text.toLowerCase()] ||
    text ||
    '未提供'
  );
}
export const highRisk = (value: unknown) =>
  ['high', '高'].includes(
    String(value ?? '')
      .trim()
      .toLowerCase(),
  );
export function quantity(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) return null;
  return value;
}
export function warehouseRange(min: unknown, max: unknown): string {
  const lo = quantity(min),
    hi = quantity(max);
  if (lo !== null && hi !== null)
    return hi < lo ? '数据异常' : lo === hi ? String(lo) : `${lo}–${hi}`;
  if (lo !== null) return `${lo}+`;
  if (hi !== null) return `最多 ${hi}`;
  return '未提供';
}
export const warehouseEmpty = (min: unknown, max: unknown) =>
  quantity(max) === 0 && (quantity(min) === 0 || min == null);
export const labels: Record<string, string> = {
  'Main Color': '主要颜色',
  'Main Material': '主要材质',
  sku: '供应商 SKU',
  mpn: '卖家商品编码',
  productName: '产品名称',
  description: '产品描述',
  characteristics: '产品特点',
  category: '产品类目',
  categoryPath: '类目路径',
  categoryCode: '类目代码',
  mainColor: '颜色',
  mainMaterial: '材质',
  placeOfOrigin: '产地',
  customized: '支持定制',
  whiteLabel: '白牌产品',
  lithiumBatteryContained: '含锂电池',
  upc: 'UPC',
  comboFlag: '组合商品',
  partFlag: '配件',
  overSizeFlag: '超大件 LTL',
  isLGP: '德国大件包裹',
  isMsf: '可参加 Amazon MSF',
  newArrivalFlag: '新品',
  skuAvailable: '可购买',
  firstArrivalDate: '首次到库日期',
  brandInfo: '品牌信息',
  brandName: '品牌名称',
  brandPictures: '品牌图片',
  brandIntro: '品牌介绍',
  assembledLength: '组装长度',
  assembledWidth: '组装宽度',
  assembledHeight: '组装高度',
  assembledWeight: '产品重量',
  assembledLengthUnit: '尺寸单位',
  assembledWeightUnit: '重量单位',
  length: '包装长度',
  width: '包装宽度',
  height: '包装高度',
  lengthUnit: '包装尺寸单位',
  weight: '包装重量',
  weightUnit: '包装重量单位',
  lengthCm: '包装长度（cm）',
  widthCm: '包装宽度（cm）',
  heightCm: '包装高度（cm）',
  weightKg: '包装重量（kg）',
  attributes: '产品属性',
  customList: '自定义规格',
  customName: '属性名',
  customValue: '属性值',
  customType: '属性类型',
  associateProductList: '关联商品 SKU',
  associateProductInfo: '关联商品',
  itemCode: '商品编码',
  name: '名称',
  comboInfo: '组合配件',
  qty: '数量',
  mainImageUrl: '主图',
  imageUrls: '附图',
  fileUrls: '文档资料',
  videoUrls: '视频资料',
  productVideoUrl: '产品视频',
  certificationList: '认证文件',
  title: '标题',
  url: '链接',
  afterSalesPolicy: '售后政策',
  protectionServiceRates: '保障服务费率',
  protectionService: '保障服务',
  rate: '费率',
  remoteAreaPostalCodes: '偏远地区邮编',
  shipFromCountry: '发货国家',
  postalCodesList: '邮编范围',
  shipToCountry: '收货国家',
  shipToPostCodes: '邮编',
  unAvailablePlatform: '不可售平台',
  id: '编号',
  returnRate: '商品退货率',
  sellerInfo: '卖家信息',
  sellerStore: '卖家名称',
  sellerCode: '卖家代码',
  sellerType: '卖家类型',
  gigaIndex: '卖家 GIGA Index',
  sellerReturnRate: '卖家退货率',
  sellerReturnApprovalRate: '退货批准率',
  sellerMessageResponseRate: '站内信回复率',
  currency: '币种',
  price: '商品原价',
  shippingFee: '普通地区物流费（含打包）',
  remoteShippingFee: '偏远地区物流费',
  shippingFeeRange: '物流费区间',
  minAmount: '最低费用',
  maxAmount: '最高费用',
  exclusivePrice: '专享价',
  exclusivePriceExpireTime: '专享价有效期（UTC−8）',
  discountedPrice: '活动折扣价',
  promotionFrom: '活动开始（UTC−8）',
  promotionTo: '活动结束（UTC−8）',
  purchaseLimit: '活动限购数量',
  mapPrice: '最低零售价 MAP',
  srpPrice: '建议零售价 SRP',
  futureMapPrice: '未来 MAP',
  effectMapTime: '未来 MAP 生效时间',
  spotPrice: '阶梯价',
  minQuantity: '最小购买数量',
  maxQuantity: '最大购买数量',
  discountedSpotPrice: '阶梯折扣价',
  limit: '限购数量',
  rebatesPrice: '返点价格',
  days: '合约天数',
  rebateAmount: '返点金额',
  rebatePrice: '返点后单价',
  marginPrice: '现货协议价',
  futurePrice: '期货协议价',
  deliveryDate: '交割日期',
  totalQuantityAvailable: '总可购买数量',
  depositPercentage: '保证金比例',
  directSettlement: '尾款交割',
  marginTransaction: '转现货保证金',
  unitPrice: '协议价格',
  minEstimatedOceanFreight: '预计海运费下限',
  maxEstimatedOceanFreight: '预计海运费上限',
  internationalFulfillmentFees: '跨国物流费',
  internationalFulfillmentFee: '跨国物流费（含打包）',
  remoteIntlFulfillmentFee: '跨国偏远地区物流费',
  countryName: '国家',
  countryCode: '国家代码',
  buyerInventoryInfo: 'Buyer 自有库存',
  sellerInventoryInfo: 'Seller 可售库存',
  sellerAvailableInventory: '可售库存总量',
  discountAvailableInventory: '促销可购数量',
  sellerInventoryDistribution: 'Seller 分仓库存',
  warehouseCode: '仓库代码',
  availableQtyMin: '可购数量下限',
  availableQtyMax: '可购数量上限',
  nextArrivalInventory: '下次预计到库',
  nextArrivalBegin: '预计到货起始日期',
  nextArrivalEnd: '预计到货截止日期',
  nextArrivalQtyMin: '预计到货下限',
  nextArrivalQtyMax: '预计到货上限',
  totalBuyerAvailableInventory: 'Buyer 自有可用库存',
  totalSharedInventory: '可用共享库存',
  totalMarginInventory: '未付尾款现货库存',
  totalFutureInventory: '未付尾款期货库存',
  totalSystemLockedInventory: '系统锁定库存',
  totalBuyerLockedInventory: 'Buyer 手动锁定库存',
  buyerInventoryDistribution: 'Buyer 分仓库存',
  buyerAvailableInventory: '自有可用库存',
  marginInventory: '未付尾款库存',
  sharedInventory: '共享库存',
  systemLockedInventory: '系统锁定',
  buyerLockedInventory: '手动锁定',
  totalStorageFee: '累计仓租',
  unpaidStorageFee: '未支付仓租',
};
export const label = (key: string) => labels[key.trim()] || key;
