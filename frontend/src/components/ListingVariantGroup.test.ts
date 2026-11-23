import { createSSRApp } from 'vue';
import { renderToString } from '@vue/server-renderer';
import { describe, expect, it } from 'vitest';
import ListingVariantGroup from './ListingVariantGroup.vue';
import ListingSchemaField from './ListingSchemaField.vue';
import { reviewVariantValue } from '../utils/listingVariants';
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
  specs: {
    Chairs: {
      digest: 'test',
      labels: {},
      sections: {
        visible: {
          properties: {
            color: { type: 'string' },
            finish: { type: 'string' },
            size: { type: 'string' },
          },
        },
      },
    },
  },
  disabled: false,
  editableSkus: ['red', 'blue'],
};

describe('variant review table', () => {
  it('renders only the actual color dimension, without fixed finish/size columns', async () => {
    const html = await renderToString(createSSRApp(ListingVariantGroup, props));
    const table = html.slice(html.indexOf('<table'), html.indexOf('</table>'));
    for (const value of ['Red', 'Blue']) expect(table).toContain(`value="${value}"`);
    expect(table).not.toContain('disabled');
    expect(table).toContain('颜色');
    expect(table).not.toContain('饰面');
    expect(table).not.toContain('尺寸');
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
        specs: {},
      }),
    );
    expect(html).toContain('刷新类目属性');
    expect(html).toContain('类目规格尚未加载或已过期');
    expect(html).toContain('人工复验中');
  });
  it('uses each SKU schema so a string/array difference cannot hide an existing value', async () => {
    const data = structuredClone(rows);
    data[1].product_type = 'ArrayColor';
    data[1].visible.color = ['Blue'];
    const html = await renderToString(
      createSSRApp(ListingVariantGroup, {
        ...props,
        rows: data,
        specs: {
          ...props.specs,
          ArrayColor: {
            digest: 'array',
            labels: {},
            sections: {
              visible: {
                properties: { color: { type: 'array', items: { type: 'string' } } },
              },
            },
          },
        },
      }),
    );
    expect(html).toContain('value="Red"');
    expect(html).toContain('value="Blue"');
    expect(html).toContain('aria-label="颜色 1"');
  });
  it('reads the canonical SKU value in both editors instead of a stale group snapshot', async () => {
    const data = structuredClone(rows);
    // The child editor and table both write to the same row.visible object.
    data[0].visible.color = 'Child editor Brown';
    let html = await renderToString(createSSRApp(ListingVariantGroup, { ...props, rows: data }));
    expect(html).toContain('value="Child editor Brown"');
    reviewVariantValue(data, group.skus, props.editableSkus, 'red', 'color', 'Table Coffee');
    html = await renderToString(
      createSSRApp(ListingSchemaField, {
        name: 'color',
        schema: { type: 'string' },
        modelValue: data[0].visible.color,
        labels: props.labels,
        path: 'visible.color',
      }),
    );
    expect(html).toContain('value="Table Coffee"');
    expect(data[1].visible.color).toBe('Blue');
  });
});
