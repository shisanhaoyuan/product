import { describe, expect, it } from 'vitest';
import { fitDialog, resizeDialog, type ResizeEdge } from './dialogGeometry';
const box = { left: 160, top: 36, width: 960, height: 648 };
const view = { width: 1280, height: 720 };
describe('floating product archive window', () => {
  it('moves an oversized window into a smaller viewport', () => {
    expect(fitDialog(box, { width: 390, height: 500 })).toEqual({
      left: 8,
      top: 8,
      width: 374,
      height: 484,
    });
    expect(box).toEqual({ left: 160, top: 36, width: 960, height: 648 });
  });
  it.each<ResizeEdge>(['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'])(
    'keeps %s resizing within the viewport',
    (edge) => {
      for (const delta of [-10000, 10000]) {
        const result = resizeDialog(box, edge, delta, delta, view);
        expect(result.left).toBeGreaterThanOrEqual(8);
        expect(result.top).toBeGreaterThanOrEqual(8);
        expect(result.left + result.width).toBeLessThanOrEqual(view.width - 8);
        expect(result.top + result.height).toBeLessThanOrEqual(view.height - 8);
        expect(result.width).toBeGreaterThanOrEqual(360);
        expect(result.height).toBeGreaterThanOrEqual(360);
        if (!edge.includes('w')) expect(result.left).toBe(box.left);
        if (!edge.includes('e')) expect(result.left + result.width).toBe(box.left + box.width);
        if (!edge.includes('n')) expect(result.top).toBe(box.top);
        if (!edge.includes('s')) expect(result.top + result.height).toBe(box.top + box.height);
      }
    },
  );
  it('changes only the requested edge and supports repeated resize then drag', () => {
    const small = resizeDialog(box, 'e', -500, 0, view);
    expect(small).toEqual({ left: 160, top: 36, width: 460, height: 648 });
    const moved = fitDialog({ ...small, left: 850, top: 55 }, view);
    expect(moved).toEqual({ left: 812, top: 55, width: 460, height: 648 });
    const expanded = resizeDialog(moved, 'w', -200, 0, view);
    expect(expanded).toEqual({ left: 612, top: 55, width: 660, height: 648 });
  });
  it('keeps controls reachable when the viewport is smaller than the normal minimum', () => {
    const tiny = { width: 320, height: 300 };
    for (const edge of ['nw', 'se'] as ResizeEdge[]) {
      const result = resizeDialog(box, edge, 10000, -10000, tiny);
      expect(result).toEqual({ left: 8, top: 8, width: 304, height: 284 });
    }
  });
});
