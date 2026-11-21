<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import type { SourceCapture } from '../api/types';
import { catalogApi } from '../api/client';
import {
  collectProductSources,
  hasCapturedPrice,
  hasCollectedImages,
} from '../utils/sourceCollection';
import { errorMessage, money, safeUrl } from '../utils/display';
import { useProductEditing } from '../composables/useProductEditing';
import AppIcon from './AppIcon.vue';

const props = withDefaults(
  defineProps<{
    listingId: string;
    asin: string;
    sku?: string;
    variants?: { sku: string; asin: string }[];
    automatic?: boolean;
    refreshKey?: object | string;
  }>(),
  { automatic: true },
);
const editing = useProductEditing();
const record = ref<SourceCapture | null>(null);
const busy = ref(false),
  loading = ref(false),
  error = ref(''),
  progress = ref('');
let controller: AbortController | undefined;
const autoAttempted = new Set<string>();
const disabled = computed(
  () => busy.value || loading.value || !!editing?.busy.value || !!editing?.active.value,
);
const facts = computed(() => [
  ...(record.value?.wraith_test ? [['验证测试', record.value.wraith_test.message]] : []),
  ...(record.value?.method === 'wraith_background' ? [['采集方式', 'Wraith 后台浏览器']] : []),
  ['来源卖家', record.value?.seller || '未核验'],
  ['商品价格 · USD', record.value?.price ? money(record.value.price) : '未核验'],
  ['运费 · USD', record.value?.shipping != null ? money(record.value.shipping) : '未核验'],
  ['配送时间', record.value?.delivery || '未核验'],
  ...(record.value?.original_price ? [['原价 · USD', money(record.value.original_price)]] : []),
  ...(record.value?.delivery_location ? [['配送地区', record.value.delivery_location]] : []),
]);
watch(
  () => [props.listingId, props.sku, props.asin, props.refreshKey],
  async () => {
    controller?.abort();
    const request = new AbortController();
    controller = request;
    record.value = null;
    busy.value = false;
    progress.value = '';
    error.value = '';
    loading.value = true;
    try {
      const result = await catalogApi.sourceCapture(props.listingId, props.sku, request.signal);
      if (!request.signal.aborted) record.value = result;
    } catch (e) {
      if (!request.signal.aborted) error.value = errorMessage(e);
    } finally {
      if (!request.signal.aborted) loading.value = false;
    }
    if (!request.signal.aborted && !error.value) await autoCapture();
  },
  { immediate: true },
);

async function autoCapture() {
  const key = JSON.stringify([props.listingId, props.sku, props.asin]);
  if (!editing?.enabled.value || disabled.value || !props.asin || autoAttempted.has(key)) return;
  if (!props.automatic) return;
  if (
    !props.variants?.length &&
    hasCapturedPrice(record.value, props.asin) &&
    hasCollectedImages(record.value)
  )
    return;
  autoAttempted.add(key);
  await capture(false, true);
}
watch(
  () => [editing?.enabled.value, editing?.busy.value, editing?.active.value],
  () => {
    if (!loading.value && !error.value) void autoCapture();
  },
);

async function capture(reuse = false, missingOnly = false) {
  if (disabled.value) return;
  const request = new AbortController();
  controller = request;
  busy.value = true;
  if (editing) editing.busy.value = true;
  error.value = '';
  progress.value = '';
  try {
    if (reuse) {
      const result = await catalogApi.syncSource(props.listingId, props.sku, request.signal);
      if (!request.signal.aborted) record.value = result;
    } else {
      const result = await collectProductSources(
        props.listingId,
        [{ sku: props.sku, asin: props.asin }, ...(props.sku ? [] : props.variants || [])],
        request.signal,
        missingOnly,
        (text) => {
          progress.value = text;
        },
        (sku, result) => {
          if (sku === props.sku) record.value = result;
        },
      );
      if (result.errors.length) error.value = result.errors.join('；');
      progress.value = `本次取得 ${result.collected} 条商品／子 SKU 记录，其中 ${result.verifiedPrices} 条已核验来源价格`;
    }
    if (request.signal.aborted) return;
    try {
      await editing?.refresh?.();
    } catch {
      error.value = '采集结果已保存，但页面刷新失败，请刷新后查看同步信息。';
    }
  } catch (e) {
    if (controller === request) error.value = errorMessage(e);
  } finally {
    if (controller === request) {
      busy.value = false;
      if (editing) editing.busy.value = false;
    }
  }
}
onBeforeUnmount(() => controller?.abort());
</script>

<template>
  <div class="source-capture">
    <div class="source-capture-head">
      <h3>来源采集</h3>
      <button
        v-if="editing?.enabled.value && record"
        class="btn small"
        :disabled="disabled"
        @click="capture(true)"
      >
        补齐到商品档案
      </button>
      <button
        v-if="editing?.enabled.value"
        class="btn small"
        :disabled="disabled || !asin"
        @click="capture(false)"
      >
        <AppIcon name="RotateCcw" />{{
          busy ? '正在采集…' : record ? '重新采集' : '采集报价与图片'
        }}
      </button>
    </div>
    <p v-if="busy" class="muted" role="status">
      {{ progress || '正在后台采集卖家、原价和图片…' }}
    </p>
    <p v-if="!busy && progress" class="muted" role="status">{{ progress }}</p>
    <p v-if="loading" class="muted" role="status">正在读取采集记录…</p>
    <template v-else-if="record">
      <dl class="source-capture-grid">
        <div v-for="[label, value] in facts" :key="label">
          <dt>{{ label }}</dt>
          <dd>{{ value }}</dd>
        </div>
      </dl>
      <p v-if="record.image_collection" class="source-capture-meta" role="status">
        图片采集：{{ record.image_collection.message }}
      </p>
      <details v-if="record.image_collection?.failures.length">
        <summary>查看图片采集失败原因</summary>
        <p v-for="failure in record.image_collection.failures" :key="failure.index">
          第 {{ failure.index }} 张：{{ failure.reason }}
        </p>
      </details>
      <p v-if="record.sync?.filled.length" class="source-capture-meta" role="status">
        已同步补齐商品档案 {{ record.sync.filled.length }} 处信息。
      </p>
      <p v-if="record.sync?.preserved.length" class="source-capture-meta">
        已有填写值已保留，请对照本次来源信息核对。
      </p>
      <p class="source-capture-meta">
        <a
          v-if="safeUrl(record.source_url)"
          :href="safeUrl(record.source_url)"
          target="_blank"
          rel="noopener noreferrer"
          >{{ record.asin }} ↗</a
        >
        <span>{{
          record.captured_at
            ? new Date(record.captured_at).toLocaleString('zh-CN', { hour12: false })
            : ''
        }}</span>
      </p>
      <ul v-if="record.issues?.length" class="source-capture-issues">
        <li v-for="issue in record.issues" :key="issue">{{ issue }}</li>
      </ul>
    </template>
    <p v-else class="muted">{{ sku ? '该子 SKU 暂无来源采集记录' : '主商品暂无来源采集记录' }}</p>
    <button v-if="busy" class="btn small" @click="controller?.abort()">
      <AppIcon name="X" />取消采集
    </button>
    <p v-if="error" role="alert" class="inline-error">{{ error }}</p>
  </div>
</template>
