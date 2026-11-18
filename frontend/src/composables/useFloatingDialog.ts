import { computed, onBeforeUnmount, onMounted, ref, type CSSProperties, type Ref } from 'vue';
import {
  fitDialog,
  resizeDialog,
  type DialogBounds,
  type ResizeEdge,
} from '../utils/dialogGeometry';

export function useFloatingDialog(dialog: Ref<HTMLDialogElement | undefined>) {
  const position = ref<{ left: number; top: number }>();
  const size = ref<{ width: number; height: number }>();
  const dragging = ref(false);
  const resizing = ref(false);
  const edges: ResizeEdge[] = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'];
  let gesture:
    { pointer: number; x: number; y: number; box: DialogBounds; edge?: ResizeEdge } | undefined;
  let observer: ResizeObserver | undefined;
  const viewport = () => ({ width: window.innerWidth, height: window.innerHeight });
  const style = computed<CSSProperties>(() => ({
    ...(position.value
      ? {
          position: 'fixed',
          left: position.value.left + 'px',
          top: position.value.top + 'px',
          right: 'auto',
          bottom: 'auto',
          margin: '0',
        }
      : {}),
    ...(size.value
      ? {
          width: size.value.width + 'px',
          height: size.value.height + 'px',
          maxHeight: 'calc(100dvh - 16px)',
          maxWidth: 'calc(100vw - 16px)',
        }
      : {}),
  }));
  function apply(box: DialogBounds, resized: boolean) {
    position.value = { left: box.left, top: box.top };
    if (resized) size.value = { width: box.width, height: box.height };
  }
  function keepVisible() {
    const box = dialog.value?.getBoundingClientRect();
    if (!box || !position.value) return;
    const fitted = fitDialog(box, viewport());
    if (
      fitted.left !== box.left ||
      fitted.top !== box.top ||
      fitted.width !== box.width ||
      fitted.height !== box.height
    )
      apply(fitted, !!size.value);
  }
  function interactive(event: PointerEvent | MouseEvent) {
    return (event.target as Element).closest('button, a, input, select, textarea');
  }
  function start(event: PointerEvent, edge?: ResizeEdge) {
    if (!event.isPrimary || event.button !== 0 || gesture || (!edge && interactive(event))) return;
    const box = dialog.value?.getBoundingClientRect();
    if (!box) return;
    gesture = {
      pointer: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      box: { left: box.left, top: box.top, width: box.width, height: box.height },
      edge,
    };
    apply(gesture.box, !!edge);
    dragging.value = !edge;
    resizing.value = !!edge;
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    event.preventDefault();
  }
  function move(event: PointerEvent) {
    if (!gesture || gesture.pointer !== event.pointerId) return;
    const { box, edge } = gesture,
      dx = event.clientX - gesture.x,
      dy = event.clientY - gesture.y;
    apply(
      edge
        ? resizeDialog(box, edge, dx, dy, viewport())
        : fitDialog({ ...box, left: box.left + dx, top: box.top + dy }, viewport()),
      !!edge,
    );
  }
  function end(event: PointerEvent) {
    if (!gesture || gesture.pointer !== event.pointerId) return;
    gesture = undefined;
    dragging.value = resizing.value = false;
    const handle = event.currentTarget as HTMLElement;
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
  }
  function reset(event?: MouseEvent) {
    if (event && interactive(event)) return;
    position.value = size.value = undefined;
  }
  onMounted(() => {
    observer = new ResizeObserver(keepVisible);
    if (dialog.value) observer.observe(dialog.value);
    window.addEventListener('resize', keepVisible);
  });
  onBeforeUnmount(() => {
    observer?.disconnect();
    window.removeEventListener('resize', keepVisible);
  });
  return { style, dragging, resizing, edges, start, move, end, reset };
}
