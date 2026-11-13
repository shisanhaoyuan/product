<script setup lang="ts">
import { computed } from 'vue';
import AttributeValue from './AttributeValue.vue';
import { groupedAttributes } from '../utils/attributeLayout';
import { lockedAttributeKeys } from '../composables/useProductEditing';
import ReadableAttributes from './ReadableAttributes.vue';
import InlineEdit from './InlineEdit.vue';
const props = defineProps<{ fields: Record<string, unknown>; query: string }>();
const sources: Record<string, { title: string; note: string }> = {
  dynamic_attributes: { title: '商品属性', note: '导入记录中的动态属性，按信息用途分组。' },
  raw_saved_template: {
    title: '保存模板记录',
    note: '导入文件保留的模板字段，供对照查看；与编辑器回读分开展示。',
  },
  live_schema_values: {
    title: '编辑器回读',
    note: '采集时编辑框中的实际值，供对照查看；不等同于重新打开后的保存验证。',
  },
};
const blocks = computed(() =>
  Object.entries(props.fields)
    .filter(([key]) => key in sources)
    .map(([key, value]) => {
      const source = sources[key] || { title: key, note: '商品档案中的独立字段。' };
      const sourceMatch = `${key} ${source.title}`
        .toLowerCase()
        .includes(props.query.trim().toLowerCase());
      const query = sourceMatch ? '' : props.query;
      const count = groupedAttributes(value, query).reduce(
        (sum, group) => sum + group.rows.length,
        0,
      );
      return { key, value, ...source, query, count };
    })
    .filter((block) => block.count > 0),
);
const otherFields = computed(() =>
  Object.fromEntries(Object.entries(props.fields).filter(([key]) => !(key in sources))),
);
const otherCount = computed(() =>
  groupedAttributes(otherFields.value, props.query).reduce(
    (sum, group) => sum + group.rows.length,
    0,
  ),
);
const primary = computed(
  () =>
    blocks.value.find((block) => block.key === 'dynamic_attributes')?.key || blocks.value[0]?.key,
);
</script>
<template>
  <div class="attribute-sources">
    <details
      v-if="Object.keys(otherFields).length && otherCount"
      class="attribute-source"
      :open="!!query.trim() || !('dynamic_attributes' in fields)"
    >
      <summary class="attribute-source-header">
        <div>
          <strong>{{ 'dynamic_attributes' in fields ? '其他填写字段' : '商品属性' }}</strong>
          <p>按用途分组查看，保留各字段原有编辑权限。</p>
        </div>
        <span class="attribute-source-count">{{ otherCount }} 项</span
        ><span class="attribute-source-chevron" aria-hidden="true">⌄</span>
      </summary>
      <div class="attribute-source-body">
        <ReadableAttributes :value="otherFields" :query="query">
          <template #value="{ row }">
            <InlineEdit
              v-if="row.label in otherFields"
              :value="otherFields[row.label]"
              :target="{ field: 'fields', key: row.label }"
              :label="row.label"
              :readonly="lockedAttributeKeys.has(row.label.toLowerCase())"
              :kind="typeof otherFields[row.label] === 'string' ? 'multiline' : 'json'"
              >{{ row.value }}</InlineEdit
            >
            <span v-else>{{ row.value }}</span>
          </template>
        </ReadableAttributes>
        <details class="details">
          <summary>原始字段与 JSON · 查看 / 编辑</summary>
          <div v-for="(value, key) in otherFields" :key="key" class="attribute">
            <label>{{ key }}</label
            ><AttributeValue
              :value="value"
              :target="{ field: 'fields', key: String(key) }"
              :label="String(key)"
              :readonly="lockedAttributeKeys.has(String(key).toLowerCase())"
            />
          </div>
        </details>
      </div>
    </details>
    <details
      v-for="block in blocks"
      :key="block.key"
      class="attribute-source"
      :data-attribute="block.key"
      :open="!!query.trim() || block.key === primary"
    >
      <summary class="attribute-source-header">
        <div>
          <strong>{{ block.title }}</strong>
          <p>{{ block.note }}</p>
        </div>
        <span class="attribute-source-count">{{ block.count }} 项</span>
        <span class="attribute-source-chevron" aria-hidden="true">⌄</span>
      </summary>
      <div class="attribute-source-body">
        <AttributeValue
          :value="block.value"
          :target="{ field: 'fields', key: block.key }"
          :label="block.key"
          :readonly="lockedAttributeKeys.has(block.key.toLowerCase())"
          organized
          :query="block.query"
        />
      </div>
    </details>
    <p v-if="!blocks.length && (!Object.keys(otherFields).length || !otherCount)" class="empty">
      {{
        query ? '没有匹配的字段，试试中文名称、英文字段名或属性值。' : '当前商品没有填写属性记录。'
      }}
    </p>
  </div>
</template>
<style scoped>
.attribute-sources {
  display: grid;
  gap: 16px;
}
.attribute-source {
  border: 1px solid #dfe6ee;
  border-radius: 12px;
  background: #fbfcfe;
  overflow: hidden;
}
.attribute-source-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px 24px;
  cursor: pointer;
  list-style: none;
}
.attribute-source-header::-webkit-details-marker {
  display: none;
}
.attribute-source-header:hover {
  background: #f0f4f9;
}
.attribute-source-header strong {
  color: #334c6a;
  font-size: 15px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.attribute-source-header p {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.65;
  margin: 5px 0 0;
}
.attribute-source-count {
  margin-left: auto;
  flex-shrink: 0;
  color: #637d9c;
  border-radius: 6px;
  background: #eaf0f7;
  padding: 4px 8px;
  font-size: 11px;
}
.attribute-source-chevron {
  color: #71869f;
  transition: transform 150ms;
}
.attribute-source[open] > summary .attribute-source-chevron {
  transform: rotate(180deg);
}
.attribute-source-body {
  border-top: 1px solid #e5ebf2;
  padding: 24px;
}
@media (max-width: 600px) {
  .attribute-source-header {
    padding: 16px;
    gap: 10px;
  }
  .attribute-source-body {
    padding: 16px;
  }
}
</style>
