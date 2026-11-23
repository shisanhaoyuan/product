import { createSSRApp } from 'vue';
import { renderToString } from '@vue/server-renderer';
import { describe, expect, it } from 'vitest';
import ListingVariantGroup from './ListingVariantGroup.vue';
import type { ListingRow, ListingVariantGroup as Group } from '../api/publishing';

const group: Group = {
  listing_id: 'parent',
  group_id: 'JW-test',
  axes: ['color'],
  allowed_axes: ['color', 'finish'],
  primary_sku: 'red',
  skus: ['red', 'blue'],
  locked: false,
  errors: [],
  members: [
    {
      platform_sku: 'red',
      source_sku: 'Red source',
      values: { color: 'Red' },
      primary: true,
      in_batch: true,
    },
    {
      platform_sku: 'blue',
      source_sku: 'Blue source',
      values: { color: 'Blue' },
      primary: false,
      in_batch: true,
    },
  ],
};
const rows = [
  {
    platform_sku: 'red',
    visible: { color: 'Red', finish: 'Matte', size: 'Small', variantAttributeNames: ['color'] },
  },
  {
    platform_sku: 'blue',
    visible: { color: 'Blue', finish: 'Glossy', size: 'Large', variantAttributeNames: ['color'] },
  },
].map((row): ListingRow => ({
  ...row,
  listing_id: 'parent',
  source_sku: row.platform_sku,
  product_type: 'Chairs',
  fulfillment_center_id: 'node',
  identification: { mode: 'identifier', type: 'EAN', value: '', submission_id: '' },
  orderable: {},
  extra: {},
}));
const props = {
  group,
  rows,
  activeSku: 'red',
  labels: { color: '颜色', finish: '饰面', size: '尺寸' },
  schema: {
    properties: { color: { type: 'string' }, finish: { type: 'string' }, size: { type: 'string' } },
  },
  disabled: false,
  editableSkus: ['red', 'blue'],
};

describe('variant review table', () => {
  it('renders editable values for both siblings and common attributes in one table', async () => {
    const html = await renderToString(createSSRApp(ListingVariantGroup, props));
    const table = html.slice(html.indexOf('<table'), html.indexOf('</table>'));
    for (const value of ['Red', 'Blue', 'Matte', 'Glossy', 'Small', 'Large'])
      expect(table).toContain(`value="${value}"`);
    expect(table).not.toContain('disabled');
    expect(table).toContain('颜色');
    expect(table).toContain('饰面');
    expect(table).toContain('尺寸');
    expect(html).not.toContain('当前规格：');
  });
  it('keeps submitted rows read-only while allowing a failed sibling to be edited', async () => {
    const html = await renderToString(
      createSSRApp(ListingVariantGroup, { ...props, editableSkus: ['blue'] }),
    );
    expect(html).toMatch(/value="Red"[^>]*disabled/);
    expect(html).toMatch(/value="Blue"(?![^>]*disabled)/);
    expect(html).toContain('已提交，仅查看');
  });
  it('offers metadata refresh when the official spec has expired and labels manual review', async () => {
    const html = await renderToString(
      createSSRApp(ListingVariantGroup, {
        ...props,
        group: { ...group, manual: true, spec_ready: false, allowed_axes: [] },
        schema: undefined,
      }),
    );
    expect(html).toContain('刷新类目属性');
    expect(html).toContain('类目规格尚未加载或已过期');
    expect(html).toContain('人工复验中');
  });
});
