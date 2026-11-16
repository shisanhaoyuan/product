import { describe, it, expect, vi } from 'vitest';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';
import GigaPackaging from './GigaPackaging.vue';
import GigaSourceFacts from './GigaSourceFacts.vue';
import { packaging, packageDimensions, packageWeight } from '../utils/giga';

// Node has no DOMParser; these fixtures contain plain strings only.
vi.mock('../utils/display', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../utils/display')>()),
  plain: (value: unknown) => String(value ?? ''),
}));

// Example based on the reported combo; not a captured production API response.
const combo = {
  sku: 'W5755S00003',
  comboFlag: true,
  lengthUnit: 'in',
  weightUnit: 'lb',
  assembledLength: '35.00',
  assembledWidth: '41.40',
  assembledHeight: '33.00',
  assembledWeight: '56.00',
  length: null,
  width: null,
  height: null,
  weight: null,
  comboInfo: [
    { sku: 'W5755P534358', qty: 1, length: '30.7', width: '22.8', height: '9', weight: '27' },
    { sku: 'W5755P534344', qty: 1, length: '35.8', width: '30.3', height: '7.4', weight: '48' },
  ],
};

describe('GIGA packaging from the documented comboInfo fields', () => {
  it('keeps each component SKU and dimensions together, without changing source data', () => {
    const before = JSON.stringify(combo);
    const result = packaging(combo);
    expect(result.hasParent).toBe(false);
    expect(
      result.combo.map((row) => [
        row.sku,
        row.quantity,
        packageDimensions(row.fields),
        packageWeight(row.fields),
      ]),
    ).toEqual([
      ['W5755P534358', 1, '30.7 × 22.8 × 9 in', '27 lb'],
      ['W5755P534344', 1, '35.8 × 30.3 × 7.4 in', '48 lb'],
    ]);
    expect(packaging(combo)).toEqual(result);
    expect(JSON.stringify(combo)).toBe(before);
  });

  it('shows combo packaging next to assembled dimensions and keeps the original JSON available', async () => {
    const html = await renderToString(
      createSSRApp(GigaSourceFacts, { facts: { giga: { detail: combo } } }),
    );
    expect(html).toContain('组装尺寸与产品重量');
    expect(html).toContain('30.7 × 22.8 × 9 in');
    expect(html).toContain('35.8 × 30.3 × 7.4 in');
    expect(html.indexOf('包装信息')).toBeGreaterThan(html.indexOf('组装尺寸与产品重量'));
    expect(html).toContain('展开原始 JSON');
    expect(html).not.toContain('父商品包装（接口单独返回）');
    expect(html.match(/class="giga-panel giga-packaging"/g)).toHaveLength(1);
  });

  it('shows packaging even when assembled fields are absent', async () => {
    const html = await renderToString(
      createSSRApp(GigaSourceFacts, {
        facts: {
          giga: {
            detail: {
              comboInfo: combo.comboInfo,
              lengthUnit: 'in',
              weightUnit: 'lb',
            },
          },
        },
      }),
    );
    expect(html).toContain('W5755P534358');
    expect(html.match(/class="giga-panel giga-packaging"/g)).toHaveLength(1);
  });

  it('supports single products and an independently returned parent package', async () => {
    const detail = {
      sku: 'SINGLE',
      length: 10,
      width: 20,
      height: 30,
      weight: 4,
      lengthUnit: 'cm',
      weightUnit: 'kg',
    };
    expect(packaging(detail).hasParent).toBe(true);
    const html = await renderToString(createSSRApp(GigaPackaging, { detail }));
    expect(html).toContain('10 × 20 × 30 cm');
    expect(html).toContain('4 kg');
    expect(html).not.toContain('子产品');
    const both = await renderToString(
      createSSRApp(GigaPackaging, { detail: { ...combo, ...detail } }),
    );
    expect(both).toContain('父商品包装（接口单独返回）');
    expect(both).toContain('W5755P534358');
  });

  it('does not sum quantities or dimensions and does not merge repeated component SKUs', () => {
    const row = { ...combo.comboInfo[0], qty: 3 };
    const result = packaging({ ...combo, comboInfo: [row, { ...row, height: 12 }] });
    expect(result.combo.map((part) => part.quantity)).toEqual([3, 3]);
    expect(packageDimensions(result.combo[0]!.fields)).toBe('30.7 × 22.8 × 9 in');
    expect(packageDimensions(result.combo[1]!.fields)).toBe('30.7 × 22.8 × 12 in');
  });

  it('does not invent SKU, quantity, units or package weight from assembled weight', async () => {
    const result = packaging({
      assembledWeight: 56,
      comboInfo: [{ qty: '2', length: 10, width: null }],
    });
    expect(result.combo[0]!.sku).toBe('');
    expect(result.combo[0]!.quantity).toBeNull();
    expect(packageDimensions(result.combo[0]!.fields)).toBe('10 × — × — 单位未提供');
    expect(packageWeight(result.combo[0]!.fields)).toBe('未提供');
    expect(packaging({ comboInfo: null }).combo).toEqual([]);
    const html = await renderToString(
      createSSRApp(GigaPackaging, { detail: { comboFlag: true, comboInfo: [] } }),
    );
    expect(html).toContain('接口未返回组合子产品包装');
  });

  it('uses explicit component units first, preserves metric fields and unknown extras', () => {
    const result = packaging({
      ...combo,
      comboInfo: [
        {
          sku: 'METRIC',
          qty: 2,
          lengthUnit: 'cm',
          weightUnit: 'kg',
          length: 10,
          width: 20,
          height: 30,
          weight: 5,
          lengthCm: 10,
          widthCm: 20,
          heightCm: 30,
          weightKg: 5,
          futureField: 'keep',
        },
      ],
    });
    expect(packageDimensions(result.combo[0]!.fields)).toBe('10 × 20 × 30 cm');
    expect(packageWeight(result.combo[0]!.fields)).toBe('5 kg');
    expect(result.combo[0]!.extra).toEqual({ futureField: 'keep' });
    expect(packageDimensions({ lengthCm: 10, widthCm: 20, heightCm: 30 })).toBe('10 × 20 × 30 cm');
    expect(packageWeight({ weightKg: '5.50' })).toBe('5.50 kg');
    expect(packageDimensions({ length: 0, width: 0, height: 0, lengthUnit: 'in' })).toBe(
      '0 × 0 × 0 in',
    );
  });
});
