import { describe, expect, it } from 'vitest';
import { completeGroupCandidates, toggleVariantSelection } from './listingVariants';

describe('variant group submission selection', () => {
  const groups = [{ skus: ['red', 'blue'] }];
  it('selects and deselects editable siblings together without including another product', () => {
    const valid = ['red', 'blue', 'single'];
    expect(toggleVariantSelection(['single'], 'red', true, valid, groups)).toEqual([
      'single',
      'red',
      'blue',
    ]);
    expect(toggleVariantSelection(valid, 'blue', false, valid, groups)).toEqual(['single']);
  });
  it('excludes an incomplete group, but permits retry beside a successful sibling', () => {
    expect(completeGroupCandidates(['red', 'single'], ['red', 'blue', 'single'], groups)).toEqual([
      'single',
    ]);
    expect(completeGroupCandidates(['blue'], ['blue'], groups)).toEqual(['blue']);
    expect(toggleVariantSelection([], 'blue', true, ['blue'], groups)).toEqual(['blue']);
  });
});
