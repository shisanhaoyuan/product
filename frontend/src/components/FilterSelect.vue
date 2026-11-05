<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import AppIcon from './AppIcon.vue';

const props = defineProps<{
  modelValue: string;
  label: string;
  options: { value: string; label: string }[];
}>();
const emit = defineEmits<{ 'update:modelValue': [value: string] }>();
const root = ref<HTMLDetailsElement>();
const current = computed(
  () => props.options.find((item) => item.value === props.modelValue)?.label || props.label,
);

function close(restoreFocus = false) {
  if (!root.value) return;
  root.value.open = false;
  if (restoreFocus) root.value.querySelector('summary')?.focus();
}
function choose(value: string) {
  emit('update:modelValue', value);
  close(true);
}
function outside(event: PointerEvent) {
  if (event.target instanceof Node && !root.value?.contains(event.target)) close();
}
function leave(event: FocusEvent) {
  if (!root.value?.contains(event.relatedTarget as Node | null)) close();
}
onMounted(() => document.addEventListener('pointerdown', outside));
onBeforeUnmount(() => document.removeEventListener('pointerdown', outside));
</script>

<template>
  <details
    ref="root"
    class="filter-select"
    @focusout="leave"
    @keydown.esc.prevent.stop="close(true)"
  >
    <summary :aria-label="`${label}：${current}`">
      <span>{{ current }}</span>
      <AppIcon name="ChevronRight" />
    </summary>
    <div class="filter-select-options" role="group" :aria-label="label">
      <button
        v-for="item in options"
        :key="item.value"
        type="button"
        :aria-pressed="modelValue === item.value"
        @click="choose(item.value)"
      >
        <span>{{ item.label }}</span>
        <AppIcon v-if="modelValue === item.value" name="Check" />
      </button>
    </div>
  </details>
</template>

<style scoped>
.filter-select {
  position: relative;
  width: 180px;
  max-width: 100%;
}
.filter-select.category-filter {
  width: 210px;
}
summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  height: 38px;
  padding: 0 12px;
  list-style: none;
  cursor: pointer;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  color: #64758d;
  font-size: 12px;
}
summary::-webkit-details-marker {
  display: none;
}
summary > span {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
summary > .icon {
  transform: rotate(90deg);
  transition: transform 160ms;
}
summary:hover {
  border-color: #a8bbd1;
  background: #f8fafc;
}
summary:focus-visible {
  outline: 2px solid #9bb5d7;
  outline-offset: 2px;
}
[open] > summary > .icon {
  transform: rotate(-90deg);
}
.filter-select-options {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 20;
  width: 100%;
  max-height: 280px;
  overflow-y: auto;
  padding: 5px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 8px 24px #28323e14;
}
.filter-select-options button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 9px 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--ink);
  text-align: left;
  font-size: 12px;
}
.filter-select-options button:hover {
  background: #f1f5f9;
}
.filter-select-options button[aria-pressed='true'] {
  background: var(--accent-soft);
  color: var(--accent);
}
</style>
