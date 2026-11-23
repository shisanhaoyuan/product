<script setup lang="ts">
import { computed } from 'vue';
import type { ListingVariantGroup, ListingRow, ListingSpec } from '../api/publishing';
import ListingSchemaField from './ListingSchemaField.vue';
import { variantEditorColumns } from '../utils/listingVariants';
import { listingIdentityLabel } from '../utils/listingIdentity';
const props = defineProps<{
  group: ListingVariantGroup;
  rows: ListingRow[];
  activeSku: string;
  specs: Record<string, ListingSpec>;
  asins: Record<string, string>;
  labels: Record<string, string>;
  disabled: boolean;
  editableSkus: string[];
}>();
const emit = defineEmits<{
  select: [sku: string];
  axes: [axes: string[]];
  primary: [sku: string];
  value: [sku: string, key: string, value: unknown];
  refresh: [];
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
const columns = computed(() => variantEditorColumns(axes.value));
function fieldSchema(sku: string, key: string) {
  const row = props.rows.find((r) => r.platform_sku === sku);
  return row ? props.specs[row.product_type]?.sections.visible?.properties?.[key] : undefined;
}
const manual = computed(
  () =>
    props.group.manual ||
    props.rows.some((r) => props.group.skus.includes(r.platform_sku) && r.variant_manual),
);
function canEdit(sku: string) {
  return !props.disabled && props.editableSkus.includes(sku);
}
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
      <span class="group-badge">{{
        group.locked ? '沿用已提交分组' : manual ? '人工复验中' : '自动分组'
      }}</span>
    </header>
    <small class="group-id">组编号：{{ group.group_id }}</small>
    <div class="skill-mapping">
      <strong>自动匹配规则</strong>
      <p>优先同名属性，再匹配可自定义字段；必要时用 ** 合并同一规格的原始属性值。</p>
      <p>下表仅显示已选的规格区分属性，与子商品编辑区共享数据；在任一处修改都会同步。</p>
      <p v-if="manual">
        已进入人工复验：保存、校验及再次生成保留人工修改；相同规格组合会提示修正。
      </p>
      <p v-else-if="!group.locked">
        首次自动匹配后可更换差异属性和参数，人工编辑后系统不再自动替换。
      </p>
      <details v-if="group.rule_mappings?.length">
        <summary>查看属性对应依据（{{ group.rule_mappings.length }} 项）</summary>
        <div v-for="(rule, index) in group.rule_mappings" :key="index" class="mapping-row">
          <strong>{{ rule.source }} → {{ labels[rule.target] || rule.target }}</strong>
          <span>{{ rule.reason }}</span>
          <small v-if="rule.source_values[activeSku]"
            >当前规格原值：{{ rule.source_values[activeSku] }}</small
          >
        </div>
      </details>
    </div>
    <div class="group-errors" v-if="group.errors.length">
      <p v-for="(issue, index) in group.errors" :key="index">{{ issue.message }}</p>
    </div>
    <div data-listing-path="visible.variantAttributeNames">
      <strong>用哪些属性区分规格</strong>
      <button type="button" class="refresh-fields" :disabled="disabled" @click="emit('refresh')">
        刷新类目属性
      </button>
      <p v-if="!group.allowed_axes.length" class="field-notice">
        {{
          group.spec_ready === false
            ? '类目规格尚未加载或已过期，请刷新后选择。'
            : '该类目没有可选的变体属性，请刷新字段或核对商品类型。'
        }}
      </p>
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
          {{ listingIdentityLabel(member.platform_sku, asins)
          }}<template v-if="asins[member.platform_sku]"> · {{ member.platform_sku }}</template>
        </option>
      </select></label
    >
    <div class="table-heading">
      <strong>规格参数 · 同页编辑全部子 SKU</strong
      ><small>只显示当前商品用于区分规格的属性；普通属性在子商品编辑区填写。表格可左右滚动。</small>
    </div>
    <div class="variant-table-wrap">
      <table>
        <thead>
          <tr>
            <th>ASIN / 店铺 SKU</th>
            <th v-for="key in columns" :key="key">
              {{ labels[key] || key }}<small>{{ key }}</small
              ><span v-if="axes.includes(key)" class="axis-badge">分组</span>
            </th>
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
                {{ listingIdentityLabel(member.platform_sku, asins) }}</button
              ><span v-else>{{ listingIdentityLabel(member.platform_sku, asins) }}（历史成员）</span
              ><small v-if="asins[member.platform_sku]">{{ member.platform_sku }}</small>
            </td>
            <td
              v-for="key in columns"
              :key="key"
              class="variant-edit-cell"
              :data-listing-path="member.platform_sku === activeSku ? 'visible.' + key : undefined"
            >
              <ListingSchemaField
                v-if="member.in_batch && fieldSchema(member.platform_sku, key)"
                :key="member.platform_sku + '.' + key"
                :name="key"
                :schema="fieldSchema(member.platform_sku, key)!"
                :labels="labels"
                :model-value="member.values[key]"
                :path="'variants.' + member.platform_sku + '.visible.' + key"
                :required="axes.includes(key)"
                :disabled="!canEdit(member.platform_sku)"
                @update:model-value="emit('value', member.platform_sku, key, $event)"
              />
              <span v-else>{{ display(member.values[key]) }}</span>
              <small v-if="!member.in_batch || !editableSkus.includes(member.platform_sku)"
                >已提交，仅查看</small
              >
              <small v-else-if="!fieldSchema(member.platform_sku, key)">请刷新类目属性后编辑</small>
            </td>
            <td>{{ member.platform_sku === primary ? '主变体' : '子变体' }}</td>
          </tr>
        </tbody>
      </table>
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
.skill-mapping {
  margin: 12px 0;
  padding: 12px 14px;
  background: #e4eff6;
  border-radius: 10px;
}
.skill-mapping summary {
  cursor: pointer;
  margin-top: 8px;
}
.mapping-row {
  display: grid;
  gap: 4px;
  padding: 10px 0;
  border-top: 1px solid #c6d9e6;
  margin-top: 8px;
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
.table-heading {
  display: grid;
  gap: 5px;
  margin: 20px 0 10px;
}
.refresh-fields {
  margin-left: 14px;
  padding: 7px 12px;
  border: 1px solid #b6cddd;
  border-radius: 8px;
  background: white;
  color: #245f8b;
  cursor: pointer;
}
.field-notice {
  padding: 10px 0;
  color: #865020;
}
th small {
  display: block;
  font-weight: normal;
  color: #617889;
  margin-top: 4px;
}
.axis-badge {
  display: inline-block;
  margin-top: 5px;
  border-radius: 5px;
  padding: 2px 6px;
  background: #dfedf7;
  color: #315e7c;
  font-size: 11px;
}
.variant-edit-cell {
  min-width: 220px;
  vertical-align: top;
}
.variant-edit-cell :deep(.listing-field) {
  display: block;
  margin: 0;
  padding: 0;
  border: 0;
}
.variant-edit-cell :deep(.listing-field-label) {
  display: none;
}
.variant-edit-cell :deep(input),
.variant-edit-cell :deep(select),
.variant-edit-cell :deep(textarea) {
  min-width: 0;
  width: 100%;
  padding: 10px;
  font-size: 15px;
  border: 1px solid #b8ccdb;
  border-radius: 7px;
  background: white;
  box-sizing: border-box;
}
.variant-edit-cell :deep(.listing-field-control) {
  min-width: 0;
}
td:first-child {
  min-width: 185px;
  vertical-align: top;
}
td .listing-array-row button {
  text-decoration: none;
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
