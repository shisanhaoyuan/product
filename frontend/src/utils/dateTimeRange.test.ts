import { expect, it } from 'vitest';
import { dateTimeRangeLabel, dateTimeRangeQuery } from './dateTimeRange';

it('omits empty boundaries and includes the whole ending second', () => {
  expect(dateTimeRangeQuery('', '')).toEqual({});
  const range = dateTimeRangeQuery('2026-11-20T08:30:00', '2026-11-20T09:30:00');
  expect(range.created_from).toMatch(/\.000Z$/);
  expect(range.created_to).toMatch(/\.999Z$/);
  expect(Date.parse(range.created_to!) - Date.parse(range.created_from!)).toBe(3600999);
  expect(dateTimeRangeQuery('', '2026-11-20T09:30:00')).not.toHaveProperty('created_from');
  expect(dateTimeRangeQuery('2026-11-20T08:30:00', '')).not.toHaveProperty('created_to');
});

it('rejects reversed and invalid inputs instead of searching a different range', () => {
  expect(() => dateTimeRangeQuery('2026-11-21T00:00', '2026-11-20T00:00')).toThrow(
    '开始时间不能晚于结束时间',
  );
  expect(() => dateTimeRangeQuery('bad-date', '')).toThrow('有效的日期');
  expect(() => dateTimeRangeQuery('', 'bad-date')).toThrow('有效的日期');
});

it('summarizes open and closed ranges in the collapsed filter', () => {
  expect(dateTimeRangeLabel('', '')).toBe('创建时间：不限');
  expect(dateTimeRangeLabel('2026-11-20T00:00', '')).toBe('创建时间 ≥ 2026-11-20 00:00');
  expect(dateTimeRangeLabel('', '2026-11-21T23:59:59')).toBe('创建时间 ≤ 2026-11-21 23:59:59');
  expect(dateTimeRangeLabel('2026-11-20T00:00', '2026-11-21T23:59')).toContain('～');
});
