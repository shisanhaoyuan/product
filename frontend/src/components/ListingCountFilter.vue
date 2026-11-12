<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import FilterSelect from './FilterSelect.vue';

const props = defineProps<{ minimum: string; maximum: string }>();
const emit = defineEmits<{ change: [value: { minimum: string; maximum: string }] }>();
const mode = ref('');
const lower = ref('2');
const upper = ref('3');
watch(
  () => [props.minimum, props.maximum],
  ([minimum, maximum]) => {
    mode.value =
      !minimum && !maximum
        ? ''
        : minimum === '2' && !maximum
          ? 'repeat'
          : minimum === maximum
            ? 'exact'
            : !maximum
              ? 'min'
              : !minimum
                ? 'max'
                : 'range';
    lower.value = minimum || maximum || '2';
    upper.value = maximum || '3';
  },
  { immediate: true },
);
const custom = computed(() => ['exact', 'min', 'max', 'range'].includes(mode.value));
const valid = computed(() => {
  const integer = (value: string) =>
    /^\d+$/.test(value) && Number(value) >= 1 && Number(value) <= 2147483647;
  return (
    integer(lower.value) &&
    (mode.value !== 'range' || (integer(upper.value) && Number(lower.value) <= Number(upper.value)))
  );
});
function choose(value: string) {
  mode.value = value;
  if (!value || value === 'repeat') emit('change', { minimum: value ? '2' : '', maximum: '' });
}
function apply() {
  if (!valid.value) return;
  const value = String(Number(lower.value));
  emit('change', {
    minimum: mode.value === 'max' ? '' : value,
    maximum:
      mode.value === 'exact' || mode.value === 'max'
        ? value
        : mode.value === 'range'
          ? String(Number(upper.value))
          : '',
  });
}
</script>

<template>
  <div class="listing-count-filter" role="group" aria-label="上架次数筛选">
    <FilterSelect
      label="筛选上架次数"
      :model-value="mode"
      :options="[
        { value: '', label: '全部上架次数' },
        { value: 'repeat', label: '重复上架（≥2 次）' },
        { value: 'exact', label: '恰好 N 次' },
        { value: 'min', label: '至少 N 次' },
        { value: 'max', label: '至多 N 次' },
        { value: 'range', label: '次数范围' },
      ]"
      @update:model-value="choose"
    />
    <template v-if="custom">
      <input
        v-model="lower"
        type="number"
        min="1"
        max="2147483647"
        step="1"
        :aria-label="mode === 'range' ? '上架次数下限' : '上架次数'"
        @keydown.enter.prevent.stop="apply"
      />
      <template v-if="mode === 'range'">
        <span>至</span>
        <input
          v-model="upper"
          type="number"
          min="1"
          max="2147483647"
          step="1"
          aria-label="上架次数上限"
          @keydown.enter.prevent.stop="apply"
        />
      </template>
      <span>次</span>
      <button type="button" class="btn small" :disabled="!valid" @click="apply">检索</button>
      <span v-if="!valid" class="count-filter-error" role="status"
        >请输入有效正整数，下限不能大于上限</span
      >
    </template>
    <span v-if="minimum || maximum" class="count-filter-applied" role="status">
      已应用：{{
        minimum && minimum === maximum
          ? minimum
          : minimum && maximum
            ? `${minimum}–${maximum}`
            : minimum
              ? `≥${minimum}`
              : `≤${maximum}`
      }}
      次
    </span>
  </div>
</template>

<style scoped>
.listing-count-filter {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
}
input {
  width: 82px;
  height: 38px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 0 9px;
  color: var(--ink);
  background: #fff;
}
.count-filter-applied {
  color: #5279aa;
}
.count-filter-error {
  color: #a94646;
}
</style>
