import { describe, expect, it } from 'vitest';
import { groupedAttributes } from './attributeLayout';

describe('product attribute layout', () => {
  it('separates copy, dimensions, compliance and technical fields without mutating source', () => {
    const source = {
      'Brand Name': { val: ['Erguoguo'] },
      'Assembled Product Width': { val: ['{"Measure":"63.00","Unit":"in"}'] },
      'Key Features': { val: ['First fact.', 'Second fact.'] },
      'Has Written Warranty': 'No',
      ListingStageAttrsPlugin: { pgSiteID: '1' },
      Unknown: '',
      Stock: 0,
    };
    const before = JSON.stringify(source);
    const groups = groupedAttributes(source);
    expect(groups.find((g) => g.id === 'dimensions')?.rows[0]?.value).toBe('63.00 in');
    expect(groups.find((g) => g.id === 'copy')?.rows.map((r) => r.value)).toEqual([
      'First fact.',
      'Second fact.',
    ]);
    expect(groups.find((g) => g.id === 'copy')?.rows.every((r) => r.long)).toBe(true);
    expect(groups.find((g) => g.id === 'compliance')?.rows[0]?.title).toBe('书面保修');
    expect(groups.find((g) => g.id === 'internal')?.rows[0]?.label).toBe(
      'ListingStageAttrsPlugin / pgSiteID',
    );
    expect(groups.flatMap((g) => g.rows).find((r) => r.label === 'Unknown')?.empty).toBe(true);
    expect(groups.flatMap((g) => g.rows).find((r) => r.label === 'Stock')?.value).toBe('0');
    expect(JSON.stringify(source)).toBe(before);
  });
  it('searches Chinese labels, English labels and values at the field level', () => {
    const source = {
      'Brand Name': 'Erguoguo',
      Color: 'Oak',
      'Key Features': ['A fact', 'Other fact'],
    };
    for (const query of ['品牌', 'brand name', 'erguoguo']) {
      const rows = groupedAttributes(source, query).flatMap((g) => g.rows);
      expect(rows).toHaveLength(1);
      expect(rows[0]?.label).toBe('Brand Name');
    }
    expect(groupedAttributes(source, 'other fact').flatMap((g) => g.rows)).toHaveLength(1);
    expect(groupedAttributes(source, 'absent')).toEqual([]);
  });
  it('keeps long unknown text wide and treats whitespace as empty', () => {
    const groups = groupedAttributes({ Notes: 'x'.repeat(200), Blank: '   ' });
    expect(groups[0]?.rows[0]?.long).toBe(true);
    expect(groups[0]?.rows[1]?.empty).toBe(true);
  });
});
