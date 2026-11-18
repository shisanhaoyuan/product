<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import AppIcon from './AppIcon.vue';

defineProps<{
  label: string;
  items: { path: string; name: string; icon: string; count?: number }[];
  activePath?: string;
}>();
const route = useRoute();
const trigger = ref<HTMLButtonElement>();
const panel = ref<HTMLElement>();
const open = ref(false);
const position = ref({ top: '0px', left: '0px', maxHeight: '320px' });
const panelId = 'catalog-source-navigation';
function place() {
  const rect = trigger.value?.getBoundingClientRect();
  if (!rect) return;
  position.value = {
    top: `${rect.bottom + 8}px`,
    left: `${Math.max(12, Math.min(rect.left, window.innerWidth - 252))}px`,
    maxHeight: `${Math.max(80, window.innerHeight - rect.bottom - 20)}px`,
  };
}
function close(restoreFocus = false) {
  open.value = false;
  if (restoreFocus) trigger.value?.focus();
}
async function toggle(focusFirst = false) {
  open.value = focusFirst || !open.value;
  if (!open.value) return;
  place();
  if (focusFirst) {
    await nextTick();
    panel.value?.querySelector<HTMLAnchorElement>('a')?.focus();
  }
}
function outside(event: PointerEvent) {
  const node = event.target as Node;
  if (!trigger.value?.contains(node) && !panel.value?.contains(node)) close();
}
function leave(event: FocusEvent) {
  const target = event.relatedTarget as Node | null;
  if (!trigger.value?.contains(target) && !panel.value?.contains(target)) close();
}
watch(
  () => route.fullPath,
  () => close(),
);
onMounted(() => {
  document.addEventListener('pointerdown', outside);
  window.addEventListener('resize', place);
  window.addEventListener('scroll', place, true);
});
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', outside);
  window.removeEventListener('resize', place);
  window.removeEventListener('scroll', place, true);
});
</script>

<template>
  <button
    ref="trigger"
    type="button"
    class="nav-trigger"
    :class="{ active: !!activePath, expanded: open }"
    :aria-expanded="open"
    :aria-controls="panelId"
    @click="toggle()"
    @keydown.down.prevent="toggle(true)"
    @keydown.esc.prevent.stop="close(true)"
    @focusout="leave"
  >
    <span class="nav-icon"><AppIcon name="LayoutGrid" /></span>
    <span>{{ label }}</span>
    <AppIcon name="ChevronRight" class="nav-chevron" />
  </button>
  <!-- Keep the dropdown outside the mobile navigation's horizontal scroll area. -->
  <Teleport to="body">
    <div
      v-if="open"
      :id="panelId"
      ref="panel"
      class="catalog-nav-panel"
      :style="position"
      @keydown.esc.prevent.stop="close(true)"
      @focusout="leave"
    >
      <p>选择商品来源</p>
      <RouterLink
        v-for="item in items"
        :key="item.path"
        :to="item.path"
        :class="{ selected: item.path === activePath }"
        :aria-current="item.path === activePath ? 'page' : undefined"
        @click="close()"
      >
        <AppIcon :name="item.icon" />
        <span>{{ item.name }}</span>
        <span v-if="item.count !== undefined" class="catalog-nav-count">{{ item.count }}</span>
        <AppIcon v-if="item.path === activePath" name="Check" />
      </RouterLink>
    </div>
  </Teleport>
</template>

<style scoped>
.nav-trigger {
  border: 0;
  background: transparent;
  font: inherit;
}
.nav-chevron {
  width: 13px;
  height: 13px;
  transform: rotate(90deg);
  transition: transform 160ms ease;
}
.expanded .nav-chevron {
  transform: rotate(-90deg);
}
.catalog-nav-panel {
  position: fixed;
  z-index: 80;
  width: 240px;
  max-width: calc(100vw - 24px);
  overflow-y: auto;
  padding: 8px;
  border: 1px solid #d7e2ed;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 12px 36px #304d6d24;
  color: #526c85;
}
.catalog-nav-panel p {
  margin: 4px 10px 8px;
  font-size: 11px;
  color: #8393a4;
}
.catalog-nav-panel a {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 46px;
  padding: 10px;
  border-radius: 9px;
  font-size: 13px;
}
.catalog-nav-panel a > span:first-of-type {
  flex: 1;
}
.catalog-nav-panel a:hover,
.catalog-nav-panel a:focus-visible {
  background: #f0f5fa;
}
.catalog-nav-panel a.selected {
  color: #3d6599;
  background: #eaf1fa;
  font-weight: 600;
}
.catalog-nav-count {
  border-radius: 5px;
  padding: 2px 6px;
  background: #dfe8f2;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
</style>
