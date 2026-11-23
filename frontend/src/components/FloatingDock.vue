<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import { GripVertical, ChevronLeft, ChevronRight, ChevronUp, ChevronDown } from '@lucide/vue';

type Edge = 'left' | 'right' | 'top' | 'bottom';
const props = defineProps<{ label: string; storageKey: string }>();
const root = ref<HTMLElement>();
const handle = ref<HTMLButtonElement>();
const x = ref(18),
  y = ref(0),
  ready = ref(false);
const edge = ref<Edge | null>(null),
  collapsed = ref(false),
  dragging = ref(false);
let observer: ResizeObserver | undefined;
let timer: ReturnType<typeof setTimeout> | undefined;
let drag:
  | {
      id: number;
      startX: number;
      startY: number;
      x: number;
      y: number;
      moved: boolean;
      edge: Edge | null;
    }
  | undefined;
const size = () => {
  const bounds = root.value?.getBoundingClientRect();
  return { width: bounds?.width || 0, height: bounds?.height || 0 };
};
function constrain() {
  const { width, height } = size();
  x.value = Math.max(0, Math.min(x.value, innerWidth - width));
  y.value = Math.max(0, Math.min(y.value, innerHeight - height));
  if (edge.value === 'left') x.value = 0;
  if (edge.value === 'right') x.value = Math.max(0, innerWidth - width);
  if (edge.value === 'top') y.value = 0;
  if (edge.value === 'bottom') y.value = Math.max(0, innerHeight - height);
}
function persist() {
  try {
    localStorage.setItem(
      props.storageKey,
      JSON.stringify({ x: x.value, y: y.value, edge: edge.value }),
    );
  } catch {
    /* Position is optional. */
  }
}
function reveal() {
  clearTimeout(timer);
  collapsed.value = false;
}
function keepOpen() {
  clearTimeout(timer);
}
function hideLater() {
  clearTimeout(timer);
  if (!edge.value || dragging.value) return;
  timer = setTimeout(() => {
    if (!root.value?.contains(document.activeElement)) collapsed.value = true;
  }, 500);
}
function start(event: PointerEvent) {
  if (event.button !== 0) return;
  clearTimeout(timer);
  drag = {
    id: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    x: x.value,
    y: y.value,
    moved: false,
    edge: edge.value,
  };
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
}
function move(event: PointerEvent) {
  if (!drag || drag.id !== event.pointerId) return;
  const dx = event.clientX - drag.startX,
    dy = event.clientY - drag.startY;
  if (!drag.moved && Math.hypot(dx, dy) < 4) return;
  drag.moved = true;
  dragging.value = true;
  edge.value = null;
  collapsed.value = false;
  x.value = drag.x + dx;
  y.value = drag.y + dy;
  constrain();
}
function stop(event: PointerEvent, cancelled = false) {
  if (!drag || drag.id !== event.pointerId) return;
  const moved = drag.moved;
  if (cancelled) {
    x.value = drag.x;
    y.value = drag.y;
    edge.value = drag.edge;
  } else if (moved) {
    const { width, height } = size();
    const distances: [Edge, number][] = [
      ['left', x.value],
      ['right', innerWidth - width - x.value],
      ['top', y.value],
      ['bottom', innerHeight - height - y.value],
    ];
    distances.sort((a, b) => a[1] - b[1]);
    edge.value = distances[0]![1] <= 20 ? distances[0]![0] : null;
  }
  drag = undefined;
  dragging.value = false;
  constrain();
  collapsed.value = !!edge.value && (moved || cancelled);
  if (collapsed.value) handle.value?.blur();
  if (moved || cancelled) persist();
}
function keyboardMove(event: KeyboardEvent) {
  const offsets: Record<string, [number, number]> = {
    ArrowLeft: [-20, 0],
    ArrowRight: [20, 0],
    ArrowUp: [0, -20],
    ArrowDown: [0, 20],
  };
  const delta = offsets[event.key];
  if (!delta) return;
  event.preventDefault();
  edge.value = null;
  collapsed.value = false;
  x.value += delta[0];
  y.value += delta[1];
  constrain();
  persist();
}
const style = computed(() => ({
  left: `${x.value}px`,
  top: `${y.value}px`,
  visibility: ready.value ? ('visible' as const) : ('hidden' as const),
}));
const arrow = computed(
  () =>
    ({ left: ChevronRight, right: ChevronLeft, top: ChevronDown, bottom: ChevronUp })[
      edge.value || 'left'
    ],
);
function resize() {
  constrain();
}
onMounted(async () => {
  await nextTick();
  y.value = innerHeight - size().height - 18;
  try {
    const saved = JSON.parse(localStorage.getItem(props.storageKey) || 'null');
    if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
      x.value = saved.x;
      y.value = saved.y;
      edge.value = ['left', 'right', 'top', 'bottom'].includes(saved.edge) ? saved.edge : null;
      collapsed.value = !!edge.value;
    }
  } catch {
    /* Ignore an unavailable or obsolete position. */
  }
  constrain();
  ready.value = true;
  observer = new ResizeObserver(resize);
  observer.observe(root.value!);
  window.addEventListener('resize', resize);
});
onBeforeUnmount(() => {
  clearTimeout(timer);
  observer?.disconnect();
  window.removeEventListener('resize', resize);
});
</script>

<template>
  <div
    ref="root"
    class="floating-dock"
    :class="[edge && `edge-${edge}`, { collapsed, dragging }]"
    :style="style"
    :aria-label="label"
    @pointerenter="keepOpen"
    @pointerleave="hideLater"
    @focusout="hideLater"
    @focusin="keepOpen"
  >
    <div class="floating-dock-content" :inert="collapsed">
      <button
        ref="handle"
        type="button"
        class="dock-drag-handle"
        :aria-label="`拖动${label}`"
        title="拖动移动，靠近窗口边缘自动收起；方向键也可移动"
        @pointerdown="start"
        @pointermove="move"
        @pointerup="stop($event)"
        @pointercancel="stop($event, true)"
        @lostpointercapture="stop($event, true)"
        @keydown="keyboardMove"
      >
        <GripVertical :size="15" aria-hidden="true" />
      </button>
      <slot />
    </div>
    <button
      v-if="collapsed"
      type="button"
      class="dock-edge-tab"
      :aria-label="`展开${label}`"
      :title="`展开${label}`"
      @click="reveal"
    >
      <component :is="arrow" :size="15" aria-hidden="true" />
      <span>分页</span>
    </button>
  </div>
</template>

<style scoped>
.floating-dock {
  position: fixed;
  z-index: 40;
  width: max-content;
  max-width: calc(100vw - 20px);
  pointer-events: none;
  transition: transform 0.2s ease;
}
.floating-dock-content {
  display: flex;
  align-items: center;
  position: relative;
}
.dock-drag-handle {
  position: absolute;
  left: 3px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 1;
  display: grid;
  place-items: center;
  width: 18px;
  height: 30px;
  padding: 0;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #738da4;
  cursor: grab;
  touch-action: none;
  user-select: none;
  pointer-events: auto;
}
.dock-drag-handle:hover {
  color: #34536b;
  background: #dce8f1a6;
}
.dock-drag-handle:focus-visible,
.dock-edge-tab:focus-visible {
  outline: 2px solid #638bb1;
  outline-offset: -2px;
}
.dragging {
  transition: none;
}
.dragging .dock-drag-handle {
  cursor: grabbing;
}
.collapsed .floating-dock-content {
  visibility: hidden;
}
.collapsed.edge-left {
  transform: translateX(calc(-100% + 24px));
}
.collapsed.edge-right {
  transform: translateX(calc(100% - 24px));
}
.collapsed.edge-top {
  transform: translateY(calc(-100% + 24px));
}
.collapsed.edge-bottom {
  transform: translateY(calc(100% - 24px));
}
.dock-edge-tab {
  position: absolute;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 2px;
  width: 24px;
  height: 100%;
  padding: 4px 2px;
  border: 1px solid #bfd0e180;
  border-radius: 8px;
  background: #edf4fbbf;
  color: #456078;
  font-size: 11px;
  cursor: pointer;
  pointer-events: auto;
}
.edge-left .dock-edge-tab {
  top: 0;
  right: 0;
  flex-direction: column;
  border-radius: 0 8px 8px 0;
}
.edge-right .dock-edge-tab {
  top: 0;
  left: 0;
  flex-direction: column;
  border-radius: 8px 0 0 8px;
}
.edge-top .dock-edge-tab {
  bottom: 0;
  left: 0;
  width: 70px;
  height: 24px;
  border-radius: 0 0 8px 8px;
}
.edge-bottom .dock-edge-tab {
  top: 0;
  left: 0;
  width: 70px;
  height: 24px;
  border-radius: 8px 8px 0 0;
}
@media (prefers-reduced-motion: reduce) {
  .floating-dock {
    transition: none;
  }
}
</style>
