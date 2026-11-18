<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import AppIcon from './AppIcon.vue';
import { selectedValues, toggleValue } from '../utils/filterSelection';

const props = defineProps<{
  modelValue: string;
  disabled?: boolean;
  label: string;
  options: { value: string; label: string }[];
  searchable?: boolean;
  searchPlaceholder?: string;
  multiple?: boolean;
}>();
const emit = defineEmits<{ 'update:modelValue': [value: string] }>();
const root = ref<HTMLDetailsElement>();
const searchInput = ref<HTMLInputElement>();
const query = ref('');
const filteredOptions = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase();
  return props.searchable && keyword
    ? props.options.filter((item) => item.label.toLocaleLowerCase().includes(keyword))
    : props.options;
});
async function toggled() {
  query.value = '';
  if (root.value?.open && props.searchable) {
    await nextTick();
    searchInput.value?.focus();
  }
}
const selected = computed(() =>
  props.multiple ? selectedValues(props.modelValue) : [props.modelValue],
);
const current = computed(() => {
  if (!props.multiple)
    return props.options.find((item) => item.value === props.modelValue)?.label || props.label;
  if (!selected.value.length)
    return props.options.find((item) => item.value === '')?.label || props.label;
  const names = selected.value.map(
    (value) => props.options.find((item) => item.value === value)?.label || value,
  );
  return names.length === 1 ? names[0] : `${names[0]} 等 ${names.length} 项`;
});
function isSelected(value: string) {
  return props.multiple && !value ? !selected.value.length : selected.value.includes(value);
}

function close(restoreFocus = false) {
  if (!root.value) return;
  root.value.open = false;
  if (restoreFocus) root.value.querySelector('summary')?.focus();
}
function choose(value: string) {
  if (props.disabled) return;
  if (props.multiple) {
    emit('update:modelValue', toggleValue(props.modelValue, value));
    return;
  }
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
    @toggle="toggled"
  >
    <summary
      :aria-label="`${label}：${current}`"
      :aria-disabled="disabled"
      @click="disabled && $event.preventDefault()"
    >
      <span
        :title="
          multiple
            ? selected
                .map((value) => options.find((item) => item.value === value)?.label || value)
                .join('、')
            : current
        "
        >{{ current }}</span
      >
      <AppIcon name="ChevronRight" />
    </summary>
    <div class="filter-select-options" role="group" :aria-label="label">
      <p v-if="multiple" class="filter-select-hint">可多选，再次点击取消</p>
      <div v-if="searchable" class="filter-select-search">
        <input
          ref="searchInput"
          v-model="query"
          type="search"
          :aria-label="'搜索' + label"
          :placeholder="searchPlaceholder || '输入关键词搜索'"
          :disabled="disabled"
          @keydown.enter.prevent="filteredOptions.length === 1 && choose(filteredOptions[0]!.value)"
        />
      </div>
      <button
        v-for="item in filteredOptions"
        :key="item.value"
        type="button"
        :disabled="disabled"
        :aria-pressed="isSelected(item.value)"
        @click="choose(item.value)"
      >
        <span>{{ item.label }}</span>
        <AppIcon v-if="isSelected(item.value)" name="Check" />
      </button>
      <p v-if="!filteredOptions.length" class="filter-select-empty">没有匹配结果，请更换关键词</p>
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
.filter-select-search {
  position: sticky;
  top: -5px;
  padding: 5px 0 8px;
  background: #fff;
}
.filter-select-search input {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  font: inherit;
}
.filter-select-empty {
  padding: 10px;
  color: #64758d;
  font-size: 13px;
}
.filter-select-hint {
  margin: 0;
  padding: 8px 10px;
  font-size: 12px;
  color: #64758d;
}
</style>
