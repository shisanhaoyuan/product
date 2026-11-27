<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { publishingApi, type CanadaTaxCode, type CanadaTaxCodes } from '../api/publishing';
import { errorMessage } from '../utils/display';

const props = defineProps<{
  bid: string;
  modelValue?: unknown;
  disabled?: boolean;
  required?: boolean;
  issues?: string[];
  reason?: string;
}>();
const emit = defineEmits<{ 'update:modelValue': [value: number | undefined] }>();
const query = ref(''),
  offset = ref(0),
  busy = ref(false),
  error = ref('');
const results = ref<CanadaTaxCodes>(),
  selected = ref<CanadaTaxCode>();
let timer: ReturnType<typeof setTimeout> | undefined;
let searchAbort: AbortController | undefined, selectedAbort: AbortController | undefined;

watch(
  [() => props.bid, query, offset],
  () => {
    clearTimeout(timer);
    searchAbort?.abort();
    const controller = new AbortController();
    searchAbort = controller;
    busy.value = true;
    error.value = '';
    results.value = undefined;
    timer = setTimeout(async () => {
      try {
        const result = await publishingApi.canadaTaxCodes(
          props.bid,
          { q: query.value, offset: offset.value },
          controller.signal,
        );
        if (!controller.signal.aborted) results.value = result;
      } catch (e) {
        if (!controller.signal.aborted) error.value = errorMessage(e);
      } finally {
        if (!controller.signal.aborted) busy.value = false;
      }
    }, 200);
  },
  { immediate: true },
);
watch(
  [() => props.bid, () => props.modelValue],
  async () => {
    selectedAbort?.abort();
    const controller = new AbortController();
    selectedAbort = controller;
    selected.value = undefined;
    if (props.modelValue === undefined || props.modelValue === null || props.modelValue === '')
      return;
    try {
      const result = await publishingApi.canadaTaxCodes(
        props.bid,
        { code: String(props.modelValue) },
        controller.signal,
      );
      if (!controller.signal.aborted) selected.value = result.items[0];
    } catch (e) {
      if (!controller.signal.aborted) error.value = errorMessage(e);
    }
  },
  { immediate: true },
);
function search(event: Event) {
  query.value = (event.target as HTMLInputElement).value;
  offset.value = 0;
}
onBeforeUnmount(() => {
  clearTimeout(timer);
  searchAbort?.abort();
  selectedAbort?.abort();
});
</script>

<template>
  <div class="ca-tax-field" data-listing-path="orderable.productTaxCode">
    <label class="listing-field-label"
      ><span><i v-if="required" class="listing-required">*</i>商品税码</span
      ><small>Product Tax Code · 加拿大</small></label
    >
    <p class="muted">生成时按当前商品资料从税码表中自动匹配，也可搜索编码或英文说明后更改。</p>
    <div
      v-if="modelValue !== undefined && modelValue !== null && modelValue !== ''"
      class="tax-selected"
    >
      <strong>已选税码 {{ modelValue }}</strong>
      <p v-if="selected">{{ selected.description }}</p>
      <small v-if="selected"
        >{{ selected.path.join(' › ') }} · GS List 第 {{ selected.source_row }} 行</small
      >
      <p v-if="reason">{{ reason }}</p>
      <p v-if="selected && !selected.available" class="seller-risk-label">
        来源表将该编码标为保留／历史或其他国家专用，请更换。
      </p>
      <button
        v-if="!disabled"
        type="button"
        class="btn small"
        @click="emit('update:modelValue', undefined)"
      >
        清空重选
      </button>
    </div>
    <template v-if="!disabled">
      <input
        aria-label="搜索加拿大税码"
        :value="query"
        maxlength="200"
        placeholder="输入税码或英文关键词，例如 furniture、pet"
        @input="search"
      />
      <p v-if="busy" role="status">正在查询税码…</p>
      <ul v-else-if="results?.items.length" class="tax-options" aria-label="加拿大税码搜索结果">
        <li v-for="item in results.items" :key="item.code">
          <button
            type="button"
            :disabled="!item.available"
            :aria-pressed="Number(modelValue) === item.code"
            @click="emit('update:modelValue', item.code)"
          >
            <strong
              >{{ item.code }} <span v-if="Number(modelValue) === item.code">· 已选</span></strong
            >
            <span>{{ item.description }}</span>
            <small>{{ item.path.join(' › ') }}{{ item.available ? '' : ' · 不可选' }}</small>
          </button>
        </li>
      </ul>
      <p v-else-if="results">没有匹配税码，请换用编码或英文关键词。</p>
      <div v-if="results" class="tax-pages">
        <span
          >{{ results.source.version }} 税码表 · 共 {{ results.source.count }} 条 · 匹配
          {{ results.total }} 条</span
        >
        <button
          type="button"
          class="btn small"
          :disabled="offset === 0 || busy"
          @click="offset = Math.max(0, offset - 50)"
        >
          上一页
        </button>
        <button
          type="button"
          class="btn small"
          :disabled="offset + 50 >= results.total || busy"
          @click="offset += 50"
        >
          下一页
        </button>
      </div>
    </template>
    <p v-if="error" class="seller-risk-label" role="alert">{{ error }}</p>
    <p v-for="issue in issues" :key="issue" class="seller-risk-label">{{ issue }}</p>
  </div>
</template>

<style scoped>
.ca-tax-field {
  grid-column: 1 / -1;
  padding: 18px;
  border: 1px solid #c5d6e5;
  border-radius: 14px;
  background: #f3f7fb;
  min-width: 0;
}
.tax-selected {
  margin: 10px 0;
  padding: 14px;
  background: #e5eef7;
  border-radius: 10px;
  display: grid;
  gap: 7px;
}
.tax-selected p {
  margin: 0;
}
.tax-selected .btn {
  justify-self: start;
}
.tax-options {
  list-style: none;
  margin: 10px 0;
  padding: 0;
  max-height: 230px;
  overflow: auto;
}
.tax-options button {
  width: 100%;
  display: grid;
  gap: 5px;
  padding: 12px;
  border: 1px solid #d6e0e9;
  border-radius: 8px;
  margin-bottom: 6px;
  text-align: left;
  background: white;
  color: inherit;
  cursor: pointer;
  overflow-wrap: anywhere;
}
.tax-options button[aria-pressed='true'] {
  border-color: #6e99bc;
  background: #eaf2fa;
}
.tax-options button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.tax-options small,
.tax-selected small {
  color: #526a80;
}
.tax-pages {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
}
.tax-pages span {
  margin-right: auto;
}
</style>
