<script setup lang="ts">
import { computed } from 'vue';
import InlineEdit from './InlineEdit.vue';
import ReadableAttributes from './ReadableAttributes.vue';
import { attributeRows, isStructured } from '../utils/attributes';
import { plain, text } from '../utils/display';
import type { EditTarget } from '../composables/useProductEditing';

const props = defineProps<{
  value: unknown;
  label: string;
  target: EditTarget;
  readonly?: boolean;
  organized?: boolean;
  query?: string;
}>();
const structured = computed(() => isStructured(props.value));
const rows = computed(() => attributeRows(props.value));
const json = computed(() => JSON.stringify(props.value, null, 2));
function readable(value: string) {
  return /<\/?[a-z][^>]*>/i.test(value) ? plain(value) || '空' : value;
}
</script>
<template>
  <ReadableAttributes v-if="structured && organized" :value="value" :query="query" />
  <dl v-else-if="structured" class="attribute-readable">
    <div v-for="(row, index) in rows" :key="index" class="attribute-readable-row">
      <dt v-if="row.label">{{ row.label }}</dt>
      <dd>{{ readable(row.value) }}</dd>
    </div>
  </dl>
  <InlineEdit
    v-else
    :value="value"
    :target="target"
    :label="label"
    :kind="typeof value === 'string' ? 'multiline' : 'json'"
    :readonly="readonly"
  >
    <div class="value">{{ text(value) || '空' }}</div>
  </InlineEdit>
  <details class="details attribute-json">
    <summary>查看 JSON</summary>
    <InlineEdit
      :value="value"
      :target="target"
      :label="label + ' JSON'"
      kind="json"
      :readonly="readonly"
    >
      <pre class="json-block">{{ json }}</pre>
    </InlineEdit>
  </details>
</template>
