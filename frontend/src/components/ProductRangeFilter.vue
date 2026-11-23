<script setup lang="ts">
import { computed, reactive, watch } from 'vue';

type Values = Record<'stock_min' | 'stock_max' | 'price_min' | 'price_max', string>;
const props = defineProps<{ values: Values; currency: string }>();
const emit = defineEmits<{ change: [value: Values] }>();
const empty = (): Values => ({ stock_min: '', stock_max: '', price_min: '', price_max: '' });
const draft = reactive(empty());
watch(
  () => props.values,
  (values) => Object.assign(draft, values),
  { immediate: true },
);
const groups = [
  {
    key: 'stock',
    label: '库存',
    minimum: 'stock_min',
    maximum: 'stock_max',
    step: '1',
    max: '2147483647',
  },
  {
    key: 'price',
    label: '价格',
    minimum: 'price_min',
    maximum: 'price_max',
    step: '0.01',
    max: '9999999999.99',
  },
] as const;
const error = computed(() => {
  for (const group of groups) {
    const lower = draft[group.minimum],
      upper = draft[group.maximum];
    for (const value of [lower, upper]) {
      if (!value) continue;
      const pattern = group.key === 'stock' ? /^\d+$/ : /^\d+(\.\d{1,2})?$/;
      if (!pattern.test(value) || Number(value) > Number(group.max))
        return group.key === 'stock'
          ? '库存请输入有效非负整数'
          : '价格请输入有效非负金额，最多两位小数';
    }
    if (lower && upper && Number(lower) > Number(upper)) return `${group.label}下限不能大于上限`;
  }
  return '';
});
const applied = computed(() => Object.values(props.values).some(Boolean));
const pending = computed(() =>
  Object.keys(draft).some(
    (key) => draft[key as keyof Values] !== props.values[key as keyof Values],
  ),
);
function input(key: keyof Values, event: Event) {
  const element = event.target as HTMLInputElement;
  draft[key] = element.validity.badInput ? '无效' : element.value;
}
function apply() {
  if (!error.value) emit('change', { ...draft });
}
function clear() {
  Object.assign(draft, empty());
  emit('change', empty());
}
</script>

<template>
  <div class="product-range-filter" :class="{ applied }" role="group" aria-label="库存与价格范围">
    <div
      v-for="group in groups"
      :key="group.key"
      class="range-pair"
      role="group"
      :aria-label="group.label + '范围'"
    >
      <span class="range-label"
        >{{ group.label }}<small v-if="group.key === 'price'"> / {{ currency }}</small></span
      >
      <input
        :value="draft[group.minimum]"
        type="number"
        min="0"
        :max="group.max"
        :step="group.step"
        :aria-label="group.label + '下限'"
        placeholder="最低"
        @input="input(group.minimum, $event)"
        @keydown.enter.prevent.stop="apply"
      />
      <span class="range-separator">–</span>
      <input
        :value="draft[group.maximum]"
        type="number"
        min="0"
        :max="group.max"
        :step="group.step"
        :aria-label="group.label + '上限'"
        placeholder="最高"
        @input="input(group.maximum, $event)"
        @keydown.enter.prevent.stop="apply"
      />
    </div>
    <div class="range-actions">
      <button type="button" class="btn small" :disabled="!!error" @click="apply">筛选范围</button>
      <button v-if="applied || pending" type="button" class="btn ghost small" @click="clear">
        清空范围
      </button>
      <span v-if="error" class="range-error" role="status">{{ error }}</span>
      <span v-else-if="pending" class="range-note" role="status">点击筛选或按回车应用</span>
      <span v-else-if="applied" class="range-note" role="status">范围已应用</span>
    </div>
  </div>
</template>

<style scoped>
.product-range-filter {
  display: flex;
  flex: 1 0 100%;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 22px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #f0f5fb;
  font-size: 13px;
}
.product-range-filter.applied {
  border-color: #a7bfdc;
}
.range-pair,
.range-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.range-label {
  min-width: 42px;
  font-weight: 600;
  color: #486789;
}
.range-label small {
  font-size: 11px;
  font-weight: 400;
}
.range-pair input {
  width: 104px;
  min-width: 0;
  height: 38px;
  padding: 0 10px;
  border: 1px solid #c9d8e8;
  border-radius: 8px;
  background: #fff;
  color: var(--ink);
  font: inherit;
}
.range-pair input:focus-visible {
  outline: 2px solid #9bb5d7;
  outline-offset: 1px;
}
.range-note,
.range-separator {
  color: var(--muted);
  font-size: 12px;
}
.range-error {
  color: #af3f3f;
}
@media (max-width: 600px) {
  .range-pair {
    width: 100%;
    flex-wrap: nowrap;
  }
  .range-label {
    flex: 0 0 72px;
  }
  .range-pair input {
    width: 0;
    flex: 1;
  }
}
</style>
