import { describe, expect, it } from 'vitest';
import { filterGroups, selectedValues, toggleValue } from './filterSelection';

describe('multiple filter selection', () => {
  it('adds choices, toggles an existing choice off, and clears with All', () => {
    let value = toggleValue('', '21');
    value = toggleValue(value, '22');
    expect(selectedValues(value)).toEqual(['21', '22']);
    value = toggleValue(value, '21');
    expect(selectedValues(value)).toEqual(['22']);
    expect(toggleValue(value, '22')).toBe('');
    expect(toggleValue(value, '')).toBe('');
  });
  it('preserves commas in labels and supports existing single-value links', () => {
    expect(selectedValues(toggleValue('old', 'A,B'))).toEqual(['old', 'A,B']);
    expect(filterGroups('{"date":["2026-11-18","2026-11-16"]}')).toEqual({
      date: ['2026-11-18', '2026-11-16'],
    });
    expect(filterGroups('[]')).toEqual({});
    expect(filterGroups('{"date":"bad"}')).toEqual({});
  });
});
