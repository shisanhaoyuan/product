export interface DialogBounds {
  left: number;
  top: number;
  width: number;
  height: number;
}
export interface Viewport {
  width: number;
  height: number;
}
export type ResizeEdge = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw';
const gap = 8;
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(value, max));
export function fitDialog(box: DialogBounds, view: Viewport): DialogBounds {
  const width = Math.min(box.width, Math.max(1, view.width - gap * 2));
  const height = Math.min(box.height, Math.max(1, view.height - gap * 2));
  return {
    width,
    height,
    left: clamp(box.left, gap, view.width - width - gap),
    top: clamp(box.top, gap, view.height - height - gap),
  };
}
export function resizeDialog(
  box: DialogBounds,
  edge: ResizeEdge,
  dx: number,
  dy: number,
  view: Viewport,
): DialogBounds {
  box = fitDialog(box, view);
  let { left, top } = box;
  let right = left + box.width,
    bottom = top + box.height;
  const minWidth = Math.min(360, box.width),
    minHeight = Math.min(360, box.height);
  if (edge.includes('w')) left = clamp(left + dx, gap, right - minWidth);
  if (edge.includes('e')) right = clamp(right + dx, left + minWidth, view.width - gap);
  if (edge.includes('n')) top = clamp(top + dy, gap, bottom - minHeight);
  if (edge.includes('s')) bottom = clamp(bottom + dy, top + minHeight, view.height - gap);
  return { left, top, width: right - left, height: bottom - top };
}
