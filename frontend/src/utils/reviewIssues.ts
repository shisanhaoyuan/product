import type { Fields, ProductDetail } from '../api/types';

export interface ReviewIssue {
  title: string;
  message: string;
  location: string;
  section: string;
  field: string;
  sku: string;
  raw: Fields;
}
const reasons: Record<string, string> = {
  multiple_original_prices: '检测到多个原价，需要确认价格依据。',
  multiple_regular_new_sellers_offers: '有多个普通新品报价，需要确认采用哪一条。',
  sellers_panel_read_failed: '卖家报价面板读取失败，请重新核对价格和运费。',
  displayed_price_currency_or_amount_invalid: '未读到有效的美元价格，请核对来源页面。',
  latest_dual_save_not_confirmed: '未确认商品编辑器和产品库都已保存。',
  library_save_not_confirmed: '未确认产品库保存成功。',
  reopened_category_schema_not_stable: '重新打开后类目字段未加载完整。',
  persistence_unverified: '缺少保存后重新打开的核验结果。',
  persisted_value_mismatch: '保存后读取的内容与填写内容不一致。',
  'Persisted fields differ from original attempted values': '保存后读取的内容与填写内容不一致。',
  old_brand_residue: '文案中仍有旧品牌。',
  heading_or_numbering: '标题或编号格式需要调整。',
  missing_features_section: '描述中缺少功能介绍。',
  paragraph_readback_mismatch: '保存后读取的段落与填写内容不一致。',
  missing: '内容未填写。',
};
const names: Record<string, string> = {
  title: '标题',
  description: '描述',
  features: '卖点',
  'key features': '卖点',
  'copy.title': '标题',
  'copy.description': '描述',
  'copy.features': '卖点',
  price: '售价',
  dc_price: '售价',
  stock: '库存',
  dc_initlistingquantity: '库存',
  msrp: '建议零售价',
  brand: '品牌',
  'brand name': '品牌',
  ean: '商品编码',
  seller: '卖家',
  seller_name: '卖家',
  sold_by: '卖家',
  merchant_name: '卖家',
  source_price: '来源价格',
  source_shipping: '来源运费',
  delivery: '配送时间',
  delivery_time: '配送时间',
  color: '颜色',
  colour: '颜色',
  size: '尺寸',
  material: '材质',
  'product type': '商品类型',
  dc_producttype: '商品类型',
  dc_fulfillmenttype: '配送方式',
  'net content::unit': '净含量单位',
  'net content::measure': '净含量',
  dc_productid: '商品编码',
  'maximum load weight::unit': '最大承重单位',
  'maximum load weight::measure': '最大承重',
  images: '图片',
};
const fieldKeys: Record<string, string> = {
  seller: 'seller',
  title: 'copy.title',
  description: 'copy.description',
  features: 'copy.features',
  'key features': 'copy.features',
  dc_price: 'price',
  dc_initlistingquantity: 'stock',
  'brand name': 'brand',
  dc_productid: 'ean',
  seller_name: 'seller',
  sold_by: 'seller',
  merchant_name: 'seller',
};
const scalar = (v: unknown) =>
  v == null
    ? '未记录'
    : ['string', 'number', 'boolean'].includes(typeof v) && String(v).length < 100
      ? String(v)
      : '';
function reasonText(value: unknown) {
  const raw = Array.isArray(value) ? value.map(String).join('; ') : String(value || '');
  const translated = Object.entries(reasons)
    .filter(([key]) => raw.includes(key))
    .map(([, value]) => value);
  if (translated.length) return [...new Set(translated)].join(' ');
  // Keep existing human-readable Chinese; raw technical payload remains expandable.
  return /[\u4e00-\u9fff]/.test(raw) ? raw.replace(/:\s*[a-z_]+.*$/i, '，请查看原始记录。') : '';
}

export function reviewIssues(product: ProductDetail): ReviewIssue[] {
  const expanded: Fields[] = [];
  for (const issue of product.issues) {
    const details = issue.details as Fields | undefined;
    const cells = Array.isArray(details?.cells) ? (details.cells as Fields[]) : [];
    const errors = cells.filter((cell) => Array.isArray(cell.errors) && cell.errors.length);
    if (issue.section === 'copy' && errors.length) {
      expanded.push(...errors.map((cell) => ({ ...cell, section: 'copy', reason: cell.errors })));
    } else expanded.push(issue);
  }
  if (!expanded.length && !product.fill_complete) {
    expanded.push({
      section: product.local_edit?.changed ? 'local' : 'persistence',
      reason: product.local_edit?.changed
        ? '商品已有本地修改，请核对当前内容。'
        : '当前记录没有完整核验结果，请检查商品内容和来源证据。',
    });
  }
  return expanded.map((issue) => {
    const sourceSection = String(issue.section || '');
    const details =
      issue.details && typeof issue.details === 'object' ? (issue.details as Fields) : {};
    const key = String(
      issue.field_id || issue.field || (sourceSection !== 'rows' ? issue.key : '') || '',
    ).replace(/^\*/, '');
    const lower = key.toLowerCase();
    const field = fieldKeys[lower] || (lower === 'msrp' ? 'msrp' : key);
    const originalSku = String(issue.sku || (sourceSection === 'rows' ? issue.key : '') || '');
    const matches = product.variants.filter((v) => v.sku === originalSku);
    const asinMatches = product.variants.filter((v) => v.asin && v.asin === originalSku);
    const variant =
      matches.length === 1 ? matches[0] : asinMatches.length === 1 ? asinMatches[0] : undefined;
    let section = 'attributes',
      targetField = field;
    let title = names[lower] || key || '商品信息';
    let message = reasonText(issue.reason) || reasonText(issue.errors);
    if (sourceSection === 'parent_price_stock' || lower === 'parent_price_stock') {
      title = '主商品价格与库存待核对';
      targetField = product.variants.length ? '' : 'price';
      section = product.variants.length ? 'variants' : 'identity';
      message ||= product.variants.length
        ? '请逐行核对子商品的售价、倍率和库存。'
        : '请核对主商品售价、倍率和库存。';
    } else if (field === 'seller') {
      title = '卖家待核对';
      section = 'identity';
      message ||= '尚未记录卖家，可采集当前商品的来源信息后自动补齐。';
    } else if (sourceSection === 'pricing_checks') {
      title = '售价待核对';
      targetField = 'price';
      section = 'identity';
      message ||= '售价与核验值不一致，请确认价格依据和倍率。';
    } else if (sourceSection === 'stock_checks') {
      title = '库存待核对';
      targetField = 'stock';
      section = 'identity';
      message ||= '库存与来源核验结果不一致。';
    } else if (sourceSection === 'msrp_check' || lower === 'msrp') {
      title = '建议零售价待核对';
      targetField = 'msrp';
      section = 'identity';
      message ||= '建议零售价与核验值不一致。';
    } else if (sourceSection === 'required') {
      title = `必填项：${names[lower] || key || String(issue.label || '商品属性')}`;
      if (message === '请选择') message = '尚未选择，请补填。';
      message ||= '此项尚未填写，或所选值无效。';
    } else if (sourceSection === 'copy' || field.startsWith('copy.')) {
      section = 'copy';
      title = `${names[lower] || '文案'}待核对`;
      message ||= '文案有缺项或保存前后不一致。';
    } else if (sourceSection === 'rows') {
      section = 'variants';
      title = '子商品内容待核对';
      message ||= '子商品保存前后的内容不一致。';
    } else if (sourceSection === 'identity') {
      section = 'identity';
      targetField = /品牌/.test(String(issue.reason)) ? 'brand' : '';
      title = '商品档案待核对';
    } else if (
      ['save', 'save_confirmation', 'reopen', 'persistence', 'source'].includes(sourceSection)
    ) {
      section = 'evidence';
      title = '保存与来源核验';
      message ||= '缺少完整的保存或来源核验记录。';
    } else if (sourceSection === 'local') {
      section = 'copy';
      title = '本地修改待确认';
    } else if (['price', 'stock', 'msrp', 'brand', 'ean', 'images'].includes(field))
      section = 'identity';
    if (variant || originalSku) section = 'variants';
    const actual = issue.actual ?? details.actual;
    const expected = issue.expected ?? details.expected;
    if (expected !== undefined && scalar(expected) && scalar(actual))
      message += ` 记录值：${scalar(actual)}；核验值：${scalar(expected)}。`;
    if (originalSku && !variant) message += ' 原记录中的子 SKU 当前未找到，请在子商品列表核对。';
    return {
      title,
      message: message || '该项需要人工核对，详细依据见原始记录。',
      location: variant
        ? `子商品 ${variant.sku}`
        : originalSku
          ? `原子 SKU ${originalSku}`
          : {
              identity: '商品档案',
              copy: '主商品文案',
              attributes: '填写属性',
              variants: '子商品列表',
              evidence: '来源证据',
            }[section] || '商品信息',
      section,
      field: targetField,
      sku: variant?.sku || '',
      raw: issue,
    };
  });
}
