<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { JsonSchema } from '../api/publishing';
import ListingRichText from './ListingRichText.vue';
const props = defineProps<{
  schema: JsonSchema;
  name: string;
  modelValue?: unknown;
  labels: Record<string, string>;
  required?: boolean;
  disabled?: boolean;
  path: string;
  issues?: string[];
}>();
const emit = defineEmits<{ 'update:modelValue': [value: unknown] }>();
const object = computed(() =>
  props.modelValue && typeof props.modelValue === 'object' && !Array.isArray(props.modelValue)
    ? (props.modelValue as Record<string, unknown>)
    : {},
);
const list = computed(() =>
  Array.isArray(props.modelValue) ? (props.modelValue as unknown[]) : [],
);
const jsonText = ref(''),
  jsonError = ref('');
watch(
  () => props.modelValue,
  (v) => {
    jsonText.value = v === undefined ? '' : JSON.stringify(v, null, 2);
  },
  { immediate: true, deep: true },
);
const rich = computed(() =>
  ['shortDescription', 'longDescription', 'warrantyText'].includes(props.name),
);
const isPrimitiveArray = computed(
  () =>
    props.schema.type === 'array' &&
    ['string', 'number', 'integer'].includes(props.schema.items?.type || ''),
);
const keys = computed(() =>
  Object.entries(props.schema.properties || {}).sort(([a], [b]) => {
    const unit = (k: string) => (k.toLowerCase().includes('unit') ? -1 : 0);
    return unit(a) - unit(b);
  }),
);
function nested(key: string, value: unknown) {
  const next = { ...object.value };
  if (value === undefined || value === '') delete next[key];
  else next[key] = value;
  emit('update:modelValue', Object.keys(next).length ? next : undefined);
}
function scalar(e: Event) {
  const value = (e.target as HTMLInputElement).value;
  if (value === '') emit('update:modelValue', undefined);
  else if (props.schema.type === 'boolean') emit('update:modelValue', value === 'true');
  else if (['number', 'integer'].includes(props.schema.type || ''))
    emit('update:modelValue', Number(value));
  else emit('update:modelValue', value);
}
function row(index: number, value: string) {
  const next = [...list.value];
  next[index] =
    value !== '' && ['number', 'integer'].includes(props.schema.items?.type || '')
      ? Number(value)
      : value;
  emit('update:modelValue', next);
}
function objectRow(index: number, value: unknown) {
  const next = [...list.value];
  next[index] = value;
  emit('update:modelValue', next);
}
function parseJson() {
  try {
    emit('update:modelValue', jsonText.value.trim() ? JSON.parse(jsonText.value) : undefined);
    jsonError.value = '';
  } catch {
    jsonError.value = '格式不正确，尚未保存此字段。请检查括号、逗号及引号。';
  }
}
</script>
<template>
  <div
    class="listing-field"
    :class="{ 'listing-field-invalid': issues?.length }"
    :data-listing-path="path"
  >
    <label class="listing-field-label">
      <span
        ><i v-if="required" class="listing-required">*</i
        >{{ labels[name] || schema.title || name }}</span
      >
      <small>{{ schema.title && schema.title !== labels[name] ? schema.title : name }}</small>
    </label>
    <div class="listing-field-control">
      <div v-if="schema.type === 'object' && schema.properties" class="listing-object-fields">
        <ListingSchemaField
          v-for="[key, child] in keys"
          :key="key"
          :schema="child"
          :name="key"
          :model-value="object[key]"
          :labels="labels"
          :required="schema.required?.includes(key)"
          :disabled="disabled"
          :path="path + '.' + key"
          @update:model-value="nested(key, $event)"
        />
      </div>
      <select
        v-else-if="schema.enum"
        :aria-label="labels[name] || schema.title || name"
        :value="modelValue === undefined ? '' : String(modelValue)"
        :disabled="disabled"
        @change="scalar"
      >
        <option value="">请选择</option>
        <option v-for="option in schema.enum" :key="String(option)" :value="String(option)">
          {{ option }}
        </option>
      </select>
      <select
        v-else-if="schema.type === 'boolean'"
        :aria-label="labels[name] || schema.title || name"
        :value="modelValue === undefined ? '' : String(modelValue)"
        :disabled="disabled"
        @change="scalar"
      >
        <option value="">请选择</option>
        <option value="true">是 / true</option>
        <option value="false">否 / false</option>
      </select>
      <template v-else-if="isPrimitiveArray">
        <div class="listing-array-fields">
          <div v-for="(value, index) in list" :key="index" class="listing-array-row">
            <span>{{ index + 1 }}</span>
            <textarea
              v-if="name === 'keyFeatures'"
              :value="String(value)"
              :disabled="disabled"
              rows="3"
              :aria-label="`${labels[name] || name} ${index + 1}`"
              @input="row(index, ($event.target as HTMLTextAreaElement).value)"
            />
            <select
              v-else-if="schema.items?.enum"
              :value="String(value)"
              :disabled="disabled"
              :aria-label="`${labels[name] || name} ${index + 1}`"
              @change="row(index, ($event.target as HTMLSelectElement).value)"
            >
              <option value="">请选择</option>
              <option
                v-for="option in schema.items.enum"
                :key="String(option)"
                :value="String(option)"
              >
                {{ option }}
              </option>
            </select>
            <input
              v-else
              :value="String(value)"
              :type="['number', 'integer'].includes(schema.items?.type || '') ? 'number' : 'text'"
              :step="schema.items?.type === 'integer' ? 1 : 'any'"
              :disabled="disabled"
              :aria-label="`${labels[name] || name} ${index + 1}`"
              @input="row(index, ($event.target as HTMLInputElement).value)"
            />
            <button
              type="button"
              class="btn small"
              :disabled="disabled"
              :aria-label="'移除第 ' + (index + 1) + ' 项'"
              @click="
                emit(
                  'update:modelValue',
                  list.filter((_, i) => i !== index),
                )
              "
            >
              ×
            </button>
          </div>
          <button
            type="button"
            class="btn small"
            :disabled="
              disabled || (schema.maxItems !== undefined && list.length >= schema.maxItems)
            "
            @click="emit('update:modelValue', [...list, ''])"
          >
            ＋ 添加{{ labels[name] || '一项' }}
          </button>
          <small v-if="schema.minItems" class="muted"
            >沃尔玛要求至少 {{ schema.minItems }} 项，请填写真实内容。</small
          >
        </div>
      </template>
      <div
        v-else-if="schema.type === 'array' && schema.items?.type === 'object'"
        class="listing-array-fields"
      >
        <div v-for="(value, index) in list" :key="index" class="listing-array-object">
          <div class="listing-array-row">
            <strong>第 {{ index + 1 }} 项</strong
            ><button
              type="button"
              class="btn small"
              :disabled="disabled"
              @click="
                emit(
                  'update:modelValue',
                  list.filter((_, i) => i !== index),
                )
              "
            >
              移除 ×
            </button>
          </div>
          <ListingSchemaField
            :schema="schema.items"
            :name="name"
            :model-value="value"
            :labels="labels"
            :disabled="disabled"
            :path="path + '.' + index"
            @update:model-value="objectRow(index, $event)"
          />
        </div>
        <button
          type="button"
          class="btn small"
          :disabled="disabled || (schema.maxItems !== undefined && list.length >= schema.maxItems)"
          @click="emit('update:modelValue', [...list, {}])"
        >
          ＋ 添加一项
        </button>
      </div>
      <ListingRichText
        v-else-if="rich"
        :model-value="modelValue"
        :disabled="disabled"
        :label="labels[name] || name"
        @update:model-value="emit('update:modelValue', $event)"
      />
      <div
        v-else-if="schema.type === 'array' || schema.type === 'object' || !schema.type"
        class="listing-complex-field"
        :data-invalid-json="!!jsonError"
      >
        <textarea
          v-model="jsonText"
          :disabled="disabled"
          rows="4"
          :aria-label="labels[name] || name"
          @blur="parseJson"
        />
        <small v-if="jsonError" role="alert" class="seller-risk-label">{{ jsonError }}</small>
        <small v-else class="muted">此结构按沃尔玛规格填写，失焦时保存字段内容。</small>
      </div>
      <input
        v-else
        :value="modelValue"
        :type="
          ['number', 'integer'].includes(schema.type || '')
            ? 'number'
            : schema.format === 'date'
              ? 'date'
              : 'text'
        "
        :step="schema.type === 'integer' ? 1 : schema.multipleOf || 'any'"
        :min="schema.minimum"
        :max="schema.maximum"
        :maxlength="schema.maxLength"
        :disabled="disabled"
        :aria-label="labels[name] || schema.title || name"
        @input="scalar"
      />
      <p v-for="issue in issues" :key="issue" class="listing-field-error">{{ issue }}</p>
      <details v-if="schema.description || schema.comments" class="listing-field-help">
        <summary>填写说明</summary>
        <p>{{ schema.description }}</p>
        <small>{{ schema.comments }}</small>
      </details>
    </div>
  </div>
</template>
