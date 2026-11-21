<script setup lang="ts">
import { computed } from 'vue';
import type { ListingVariantGroup, ListingRow, JsonSchema } from '../api/publishing';
import ListingSchemaField from './ListingSchemaField.vue';
const props = defineProps<{
  group: ListingVariantGroup;
  rows: ListingRow[];
  activeSku: string;
  schema?: JsonSchema;
  labels: Record<string, string>;
  disabled: boolean;
}>();
const emit = defineEmits<{
  select: [sku: string];
  axes: [axes: string[]];
  primary: [sku: string];
  value: [key: string, value: unknown];
}>();
const current = computed(() => props.rows.find((r) => r.platform_sku === props.activeSku));
const axes = computed<string[]>(() => {
  const value = current.value?.visible.variantAttributeNames;
  return Array.isArray(value)
    ? value.filter((v): v is string => typeof v === 'string')
    : props.group.axes;
});
const members = computed(() =>
  props.group.members.map((m) => ({
    ...m,
    values: props.rows.find((r) => r.platform_sku === m.platform_sku)?.visible || m.values,
  })),
);
const primary = computed(
  () =>
    props.rows.find(
      (r) => props.group.skus.includes(r.platform_sku) && r.visible.isPrimaryVariant === 'Yes',
    )?.platform_sku || props.group.primary_sku,
);
function toggle(key: string) {
  emit(
    'axes',
    axes.value.includes(key) ? axes.value.filter((v) => v !== key) : [...axes.value, key],
  );
}
function display(value: unknown) {
  if (value === undefined || value === null || value === '') return '待补齐';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}
</script>
<template>
  <section class="variant-group" data-listing-path="visible.variantGroupId">
    <header>
      <div>
        <h3>多变体上架 · {{ group.members.length }} 个规格</h3>
        <p>同一来源商品、同一店铺共享一个变体组，各规格保留独立编码、价格和库存。</p>
      </div>
      <span class="group-badge">{{ group.locked ? '沿用已提交分组' : '自动分组' }}</span>
    </header>
    <small class="group-id">组编号：{{ group.group_id }}</small>
    <div class="group-errors" v-if="group.errors.length">
      <p v-for="(issue, index) in group.errors" :key="index">{{ issue.message }}</p>
    </div>
    <div data-listing-path="visible.variantAttributeNames">
      <strong>用哪些属性区分规格</strong>
      <div class="axis-options">
        <label v-for="key in group.allowed_axes" :key="key"
          ><input
            type="checkbox"
            :checked="axes.includes(key)"
            :disabled="disabled || group.locked"
            @change="toggle(key)"
          />{{ labels[key] || key }}<small>{{ key }}</small></label
        >
      </div>
      <small
        >仅显示该类目允许的变体属性。修改后保存并重新校验；首次提交须选中整组，失败后可重试失败项。</small
      >
    </div>
    <label class="primary-picker" data-listing-path="visible.isPrimaryVariant"
      >主变体<select
        :value="primary"
        :disabled="disabled || group.locked"
        @change="emit('primary', ($event.target as HTMLSelectElement).value)"
      >
        <option v-for="member in members" :key="member.platform_sku" :value="member.platform_sku">
          {{ member.source_sku }}
        </option>
      </select></label
    >
    <div class="variant-table-wrap">
      <table>
        <thead>
          <tr>
            <th>子 SKU</th>
            <th v-for="key in axes" :key="key">{{ labels[key] || key }}</th>
            <th>角色</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="member in members"
            :key="member.platform_sku"
            :class="{ active: member.platform_sku === activeSku }"
          >
            <td>
              <button
                v-if="member.in_batch"
                type="button"
                @click="emit('select', member.platform_sku)"
              >
                {{ member.source_sku }}</button
              ><span v-else>{{ member.source_sku }}（历史成员）</span
              ><small>{{ member.platform_sku }}</small>
            </td>
            <td v-for="key in axes" :key="key">{{ display(member.values[key]) }}</td>
            <td>{{ member.platform_sku === primary ? '主变体' : '子变体' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="current && axes.length" class="axis-fields">
      <strong>当前规格：{{ current.source_sku }}</strong
      ><ListingSchemaField
        v-for="key in axes.filter((k) => schema?.properties?.[k])"
        :key="key"
        :name="key"
        :schema="schema!.properties![key]"
        :labels="labels"
        :model-value="current.visible[key]"
        :path="'visible.' + key"
        :required="true"
        :disabled="disabled"
        @update:model-value="emit('value', key, $event)"
      />
    </div>
  </section>
</template>
<style scoped>
.variant-group {
  margin: 18px 0;
  padding: 20px;
  border: 1px solid #b8cddd;
  border-radius: 16px;
  background: #f0f6fa;
  color: #243c51;
}
header {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
h3 {
  margin: 0 0 6px;
  font-size: 18px;
}
p {
  margin: 4px 0;
  line-height: 1.6;
}
.group-badge {
  padding: 6px 12px;
  border-radius: 20px;
  background: #d9e8f4;
  white-space: nowrap;
}
.group-id {
  display: block;
  margin: 10px 0 16px;
  overflow-wrap: anywhere;
}
.group-errors {
  padding: 10px 14px;
  margin-bottom: 16px;
  background: #fff1ed;
  color: #9b3e28;
  border-radius: 10px;
}
.axis-options {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 10px 0;
}
.axis-options label {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 8px 12px;
  background: white;
  border: 1px solid #cad9e4;
  border-radius: 10px;
  cursor: pointer;
}
.axis-options input {
  width: 17px;
  height: 17px;
}
small {
  color: #506879;
}
.primary-picker {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 16px 0;
}
.primary-picker select {
  min-width: 0;
  max-width: 100%;
  padding: 8px;
  border: 1px solid #bacddd;
  border-radius: 8px;
  background: white;
}
.variant-table-wrap {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  background: white;
}
th,
td {
  text-align: left;
  padding: 10px;
  border-bottom: 1px solid #d9e3eb;
}
td small {
  display: block;
  margin-top: 3px;
}
td button {
  background: none;
  border: 0;
  color: #245f8b;
  cursor: pointer;
  text-decoration: underline;
  font: inherit;
}
tr.active {
  background: #e1eef7;
}
.axis-fields {
  display: grid;
  gap: 12px;
  margin-top: 18px;
}
@media (max-width: 640px) {
  .variant-group {
    padding: 12px;
  }
  .axis-options label {
    flex-wrap: wrap;
  }
}
</style>
