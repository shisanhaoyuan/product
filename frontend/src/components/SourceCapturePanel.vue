<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import type { SourceCapture } from '../api/types';
import { catalogApi } from '../api/client';
import { captureInBrowser } from '../utils/browserCapture';
import { errorMessage, money, safeUrl } from '../utils/display';
import { useProductEditing } from '../composables/useProductEditing';
import AppIcon from './AppIcon.vue';

const props = defineProps<{ listingId: string; asin: string; sku?: string }>();
const editing = useProductEditing();
const record = ref<SourceCapture | null>(null);
const busy = ref(false),
  loading = ref(false),
  error = ref(''),
  waiting = ref(false),
  helper = ref(false);
let controller: AbortController | undefined;
const disabled = computed(
  () => busy.value || loading.value || !!editing?.busy.value || !!editing?.active.value,
);
const facts = computed(() => [
  ['来源卖家', record.value?.seller || '未核验'],
  ['商品价格 · USD', record.value?.price ? money(record.value.price) : '未核验'],
  ['运费 · USD', record.value?.shipping != null ? money(record.value.shipping) : '未核验'],
  ['配送时间', record.value?.delivery || '未核验'],
  ...(record.value?.original_price ? [['原价 · USD', money(record.value.original_price)]] : []),
  ...(record.value?.delivery_location ? [['配送地区', record.value.delivery_location]] : []),
]);
watch(
  () => [props.listingId, props.sku, props.asin],
  async () => {
    controller?.abort();
    const request = new AbortController();
    controller = request;
    record.value = null;
    busy.value = waiting.value = helper.value = false;
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
  },
  { immediate: true },
);

async function capture(reuse = false) {
  if (disabled.value) return;
  const request = new AbortController();
  controller = request;
  busy.value = true;
  if (editing) editing.busy.value = true;
  error.value = '';
  helper.value = false;
  try {
    let result = await (reuse ? catalogApi.syncSource : catalogApi.captureSource)(
      props.listingId,
      props.sku,
      request.signal,
    );
    if (request.signal.aborted) return;
    if (['verification_required', 'browser_required'].includes(result.status)) {
      waiting.value = helper.value = true;
      const page = await captureInBrowser(result.asin, request.signal, 'source');
      if (request.signal.aborted) return;
      result = await catalogApi.browserCaptureSource(
        props.listingId,
        props.sku,
        page,
        request.signal,
      );
    }
    if (request.signal.aborted) return;
    if (['verification_required', 'browser_required'].includes(result.status))
      throw new Error('报价列表尚未展开，请在来源页面展开后重新采集。');
    record.value = result;
    helper.value = false;
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
      waiting.value = false;
    }
    if (editing) editing.busy.value = false;
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
          busy ? '正在采集…' : record ? '重新采集' : '采集卖家与报价'
        }}
      </button>
    </div>
    <p v-if="loading" class="muted" role="status">正在读取采集记录…</p>
    <template v-else-if="record">
      <dl class="source-capture-grid">
        <div v-for="[label, value] in facts" :key="label">
          <dt>{{ label }}</dt>
          <dd>{{ value }}</dd>
        </div>
      </dl>
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
    <p v-else class="muted">暂无来源采集记录</p>
    <p v-if="waiting" role="status" class="muted">
      等待来源页面。如出现人机验证，请在打开的网页中完成。
    </p>
    <div v-if="helper" class="actions source-helper-actions">
      <a class="btn small" href="/api/products/images/helper.zip" download
        ><AppIcon name="Download" />下载验证助手</a
      >
      <a class="btn small" href="/assets/capture-helper.html" target="_blank" rel="noopener"
        >安装说明</a
      >
      <button v-if="waiting" class="btn small" @click="controller?.abort()">
        <AppIcon name="X" />取消等待
      </button>
    </div>
    <p v-if="error" role="alert" class="inline-error">{{ error }}</p>
  </div>
</template>
