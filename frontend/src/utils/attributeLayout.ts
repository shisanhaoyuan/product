import { attributeRows, type AttributeRow } from './attributes';
import { plain } from './display';

const names: Record<string, string> = {
  'age group': '适用年龄',
  'brand name': '品牌',
  color: '颜色',
  colour: '颜色',
  condition: '商品成色',
  'product type': '商品类型',
  material: '材质',
  'assembled product depth': '成品深度',
  'assembled product height': '成品高度',
  'assembled product width': '成品宽度',
  'assembled product weight': '成品重量',
  'net content': '净含量',
  'key features': '商品卖点',
  description: '商品描述',
  title: '商品标题',
  msrp: '建议零售价',
  'manufacturer part number': '制造商零件编号',
  'recommended locations': '建议使用场所',
  'country of origin - substantial transformation': '原产国',
  'has nrtl listing certification': 'NRTL 认证',
  'has written warranty': '书面保修',
  'is prop 65 warning required': 'Prop 65 警告要求',
  'small parts warning code': '小零件警告',
};
export interface DisplayAttribute extends AttributeRow {
  title: string;
  empty: boolean;
  long: boolean;
}
export interface AttributeGroup {
  id: string;
  title: string;
  rows: DisplayAttribute[];
}
const definitions = [
  ['basic', '基本信息'],
  ['dimensions', '尺寸与重量'],
  ['copy', '标题与卖点'],
  ['compliance', '合规与保修'],
  ['other', '其他属性'],
  ['internal', '系统字段'],
];

export function groupedAttributes(value: unknown, query = ''): AttributeGroup[] {
  const groups = definitions.map(([id, title]) => ({
    id: id!,
    title: title!,
    rows: [] as DisplayAttribute[],
  }));
  for (const row of attributeRows(value)) {
    const base = row.label.split(/ · | \/ /)[0] || '';
    const key = base.toLowerCase();
    const title = names[key] ? row.label.replace(base, names[key]!) : row.label || '内容';
    const readable = /<\/?[a-z][^>]*>|&(?:#\d+|#x[\da-f]+|[a-z]+);/i.test(row.value)
      ? plain(row.value)
      : row.value;
    const empty = !readable.trim() || readable === '空';
    if (
      query.trim() &&
      !`${title} ${row.label} ${readable}`.toLowerCase().includes(query.trim().toLowerCase())
    )
      continue;
    const id =
      /^(listingstageattrsplugin|dc_|pgsiteid|platformsiteid|platformcategoryid|templetname|record_id)/i.test(
        key,
      )
        ? 'internal'
        : /key features|description|^title$/.test(key)
          ? 'copy'
          : /depth|height|width|weight|length|net content|dimension/.test(key)
            ? 'dimensions'
            : /certification|warranty|warning|prop 65|country of origin/.test(key)
              ? 'compliance'
              : /^(age group|brand name|color|colour|condition|product type|material|msrp|manufacturer part number|recommended locations)$/.test(
                    key,
                  )
                ? 'basic'
                : 'other';
    groups
      .find((group) => group.id === id)!
      .rows.push({
        ...row,
        title,
        value: empty ? '未填写' : readable,
        empty,
        long: id === 'copy' || readable.length > 160,
      });
  }
  return groups.filter((group) => group.rows.length);
}
