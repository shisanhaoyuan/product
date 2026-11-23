<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import AppIcon from './AppIcon.vue';
import { dateTimeRangeLabel, dateTimeRangeQuery } from '../utils/dateTimeRange';

const props = defineProps<{ start: string; end: string }>();
const emit = defineEmits<{ apply: [start: string, end: string] }>();
const root = ref<HTMLDetailsElement>();
const startValue = ref(''),
  endValue = ref(''),
  error = ref('');
const label = computed(() => dateTimeRangeLabel(props.start, props.end));
function prepare() {
  if (!root.value?.open) {
    startValue.value = props.start;
    endValue.value = props.end;
    error.value = '';
  }
}
function close() {
  if (root.value) root.value.open = false;
}
function apply(start = startValue.value, end = endValue.value) {
  try {
    dateTimeRangeQuery(start, end);
    emit('apply', start, end);
    close();
  } catch (value) {
    error.value = (value as Error).message;
  }
}
function outside(event: PointerEvent) {
  if (event.target instanceof Node && !root.value?.contains(event.target)) close();
}
onMounted(() => document.addEventListener('pointerdown', outside));
onBeforeUnmount(() => document.removeEventListener('pointerdown', outside));
</script>

<template>
  <details ref="root" class="time-filter" @keydown.esc.prevent.stop="close">
    <summary
      :title="label"
      :aria-label="label"
      :class="{ selected: start || end }"
      @click="prepare"
    >
      <AppIcon name="CalendarDays" /><span>{{ label }}</span
      ><AppIcon name="ChevronRight" />
    </summary>
    <form class="time-filter-panel" aria-label="创建时间范围" @submit.prevent="apply()">
      <strong>批次创建时间</strong>
      <label
        >开始时间<input
          v-model="startValue"
          type="datetime-local"
          step="1"
          aria-label="创建开始时间"
      /></label>
      <span class="range-separator">至</span>
      <label
        >结束时间<input v-model="endValue" type="datetime-local" step="1" aria-label="创建结束时间"
      /></label>
      <small>按当前电脑的本地时间筛选，可只填写开始或结束时间。</small>
      <p v-if="error" role="alert" class="time-filter-error">{{ error }}</p>
      <div class="time-filter-actions">
        <button class="btn" type="button" @click="apply('', '')">
          <AppIcon name="Trash2" />清空
        </button>
        <button class="btn primary" type="submit">应用时间</button>
      </div>
    </form>
  </details>
</template>

<style scoped>
.time-filter {
  position: relative;
  min-width: 0;
}
summary {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 42px;
  padding: 0 12px;
  list-style: none;
  cursor: pointer;
  border: 1px solid #d6e2ed;
  border-radius: 10px;
  background: #fff;
  color: #64758d;
  font-size: 13px;
}
summary::-webkit-details-marker {
  display: none;
}
summary span {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
summary svg {
  flex-shrink: 0;
}
summary svg:last-child {
  width: 14px;
  transform: rotate(90deg);
}
summary.selected {
  color: #3b6899;
  background: #edf4fc;
  border-color: #a9c4e2;
}
.time-filter-panel {
  position: absolute;
  z-index: 25;
  top: calc(100% + 8px);
  right: 0;
  width: 340px;
  max-width: calc(100vw - 60px);
  padding: 18px;
  border: 1px solid #d6e2ed;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 12px 30px #2f4c6b24;
  display: grid;
  gap: 10px;
}
.time-filter-panel label {
  display: grid;
  gap: 6px;
  color: #526a82;
  font-size: 13px;
}
.time-filter-panel input {
  width: 100%;
  min-width: 0;
  padding: 9px;
  border: 1px solid #d6e2ed;
  border-radius: 8px;
  background: #f8fafc;
  color: #344b61;
}
.range-separator {
  color: #7b8ea2;
  text-align: center;
}
.time-filter-panel small {
  color: var(--muted);
  line-height: 1.6;
}
.time-filter-actions {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin-top: 4px;
}
.time-filter-error {
  margin: 0;
  font-size: 13px;
  color: #ae4343;
}
@media (max-width: 1000px) {
  .time-filter-panel {
    right: auto;
    left: 0;
  }
}
</style>
