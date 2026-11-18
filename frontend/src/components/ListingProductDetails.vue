<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, useId, watch } from 'vue';
import { catalogApi } from '../api/client';
import type { ProductDetail } from '../api/types';
import type { ListingRow } from '../api/publishing';
import { errorMessage, money, plain, safeUrl } from '../utils/display';
import ProductImage from './ProductImage.vue';
import ProductStoreHistory from './ProductStoreHistory.vue';
import ReadableAttributes from './ReadableAttributes.vue';
import GigaSourceFacts from './GigaSourceFacts.vue';
import AppIcon from './AppIcon.vue';
import { useFloatingDialog } from '../composables/useFloatingDialog';
const props = defineProps<{ listingId: string; row?: ListingRow }>();
const emit = defineEmits<{ close: [] }>();
const titleId = useId();
const dialog = ref<HTMLDialogElement>();
const anchor = ref<HTMLElement>();
const windowHost = shallowRef<HTMLElement | string>('body');
const {
  style: windowStyle,
  dragging,
  resizing,
  edges,
  start,
  move,
  end,
  reset,
} = useFloatingDialog(dialog);
const product = ref<ProductDetail>();
const error = ref('');
const loading = ref(false);
const tab = ref<'source' | 'listing'>('source');
let requestId = 0;
const sourceRecord = computed(() => {
  const value = product.value;
  if (!value || !props.row) return value;
  // A missing child must never display another variant's copy or price.
  if (value.variants.length) {
    const matches = value.variants.filter((v) => v.sku === props.row!.source_sku);
    return matches.length === 1 ? matches[0] : undefined;
  }
  return value.sku === props.row.source_sku ? value : undefined;
});
const images = computed(() => sourceRecord.value?.images || []);
const attributes = computed(() => {
  const record = sourceRecord.value;
  return record && ('attributes' in record ? record.attributes : record.fields);
});
const sourceUrl = computed(() => safeUrl(sourceRecord.value?.source_url));
const title = computed(() =>
  plain(
    String(
      tab.value === 'listing'
        ? props.row?.visible.productName || '本次上架资料'
        : sourceRecord.value?.copy.title || product.value?.copy.title || '商品档案',
    ),
  ),
);
async function load() {
  const token = ++requestId;
  const id = props.listingId;
  error.value = '';
  loading.value = true;
  product.value = undefined;
  try {
    const result = await catalogApi.product(id);
    if (token === requestId && id === props.listingId) product.value = result;
  } catch (e) {
    if (token === requestId) error.value = errorMessage(e);
  } finally {
    if (token === requestId) loading.value = false;
  }
}
watch(
  () => props.listingId,
  () => {
    tab.value = 'source';
    void load();
  },
  { immediate: true },
);
onMounted(async () => {
  // Stay inside an existing modal preview's active subtree, if there is one.
  windowHost.value = anchor.value?.closest<HTMLDialogElement>('dialog[open]') || document.body;
  await nextTick();
  dialog.value?.show();
});
onBeforeUnmount(() => {
  requestId++;
});
</script>
<template>
  <span ref="anchor" hidden />
  <Teleport :to="windowHost">
    <dialog
      ref="dialog"
      class="product-info-dialog"
      :style="windowStyle"
      :class="{ resizing }"
      :aria-labelledby="titleId"
      aria-modal="false"
      @close="emit('close')"
      @keydown.esc.stop.prevent="dialog?.close()"
    >
      <div
        v-for="edge in edges"
        :key="edge"
        class="resize-handle"
        :class="'resize-' + edge"
        aria-hidden="true"
        @pointerdown.stop="start($event, edge)"
        @pointermove="move"
        @pointerup="end"
        @pointercancel="end"
        @lostpointercapture="end"
      />
      <header
        :class="{ dragging }"
        title="拖动标题栏移动窗口，拖动边框调整大小，双击标题栏恢复默认大小并居中"
        @pointerdown="start($event)"
        @pointermove="move"
        @pointerup="end"
        @pointercancel="end"
        @lostpointercapture="end"
        @dblclick="reset($event)"
      >
        <div>
          <span class="muted">商品档案 · {{ row?.source_sku || product?.sku || '读取中' }}</span>
          <span class="drag-hint">拖动移动 · 边框缩放 · 双击复原</span>
          <h2 :id="titleId">{{ title }}</h2>
        </div>
        <button
          class="product-info-close"
          aria-label="关闭商品档案"
          autofocus
          @click="dialog?.close()"
        >
          <AppIcon name="X" />
        </button>
      </header>
      <nav v-if="row" class="product-info-tabs" aria-label="商品详情内容">
        <button :aria-pressed="tab === 'source'" @click="tab = 'source'">商品档案</button>
        <button :aria-pressed="tab === 'listing'" @click="tab = 'listing'">本次上架资料</button>
      </nav>
      <div class="product-info-body">
        <template v-if="tab === 'source'">
          <p v-if="loading" role="status">正在读取商品档案…</p>
          <div v-else-if="error" role="alert" class="error-box">
            {{ error }} <button class="btn small" @click="load">重试</button>
          </div>
          <template v-else-if="product && sourceRecord">
            <dl class="product-info-facts">
              <dt>来源 SKU / ASIN</dt>
              <dd>{{ sourceRecord.sku }} / {{ sourceRecord.asin || '—' }}</dd>
              <dt>来源 / 卖家</dt>
              <dd>{{ product.source }} / {{ sourceRecord.seller || '未采集' }}</dd>
              <dt>类目 / 品牌</dt>
              <dd>{{ product.category || '未记录' }} / {{ product.brand || '未记录' }}</dd>
              <dt>档案价格 / 库存</dt>
              <dd>{{ money(sourceRecord.price) }} / {{ sourceRecord.stock ?? '未记录' }}</dd>
              <dt>商品编码</dt>
              <dd>{{ sourceRecord.ean || '未记录' }}</dd>
              <dt>来源页面</dt>
              <dd>
                <a v-if="sourceUrl" :href="sourceUrl" target="_blank" rel="noopener noreferrer"
                  >打开商品来源 ↗</a
                ><span v-else>未记录</span>
              </dd>
            </dl>
            <ProductStoreHistory :entries="product.store_history" />
            <h3>商品图片</h3>
            <div v-if="images.length" class="product-info-images">
              <ProductImage v-for="url in images" :key="url" :src="url" />
            </div>
            <p v-else class="muted">未记录图片</p>
            <h3>商品卖点</h3>
            <ol v-if="sourceRecord.copy.features.length">
              <li v-for="(feature, i) in sourceRecord.copy.features" :key="i">
                {{ plain(feature) }}
              </li>
            </ol>
            <p v-else class="muted">未记录卖点</p>
            <h3>商品描述</h3>
            <div class="description">
              {{ plain(sourceRecord.copy.description) || '未记录描述' }}
            </div>
            <h3>商品参数</h3>
            <ReadableAttributes :value="attributes" />
            <GigaSourceFacts
              v-if="product.source === 'GigaB2B'"
              :facts="sourceRecord.source_facts"
              :summary="product.giga_summary"
            />
            <details v-else class="details">
              <summary>来源采集信息</summary>
              <ReadableAttributes :value="sourceRecord.source_facts" />
            </details>
            <details class="details">
              <summary>展开档案原始 JSON</summary>
              <pre>{{ sourceRecord }}</pre>
            </details>
          </template>
          <p v-else-if="product" class="error-box" role="alert">
            商品档案中未找到唯一对应的子 SKU {{ row?.source_sku }}，请核对商品关联。
          </p>
        </template>
        <template v-else-if="row">
          <p class="muted">当前批次的填写内容，包含尚未保存的修改。</p>
          <dl class="product-info-facts">
            <dt>来源 SKU / 店铺 SKU</dt>
            <dd>{{ row.source_sku }} / {{ row.platform_sku }}</dd>
            <dt>类型 / 品牌</dt>
            <dd>{{ row.product_type || '未选择' }} / {{ row.visible.brand || '未填写' }}</dd>
            <dt>售价 / 商品编码</dt>
            <dd>
              {{ money(row.orderable.price as number | undefined) }} /
              {{ row.identification.value || '未填写' }}
            </dd>
          </dl>
          <h3>商品卖点</h3>
          <ol>
            <li v-for="(feature, i) in (row.visible.keyFeatures as string[]) || []" :key="i">
              {{ plain(feature) }}
            </li>
          </ol>
          <h3>商品描述</h3>
          <div class="description">
            {{ plain(String(row.visible.shortDescription || '')) || '未填写' }}
          </div>
          <h3>全部属性与履约信息</h3>
          <ReadableAttributes
            :value="{ visible: row.visible, orderable: row.orderable, extra: row.extra }"
          />
          <details class="details">
            <summary>展开上架资料 JSON</summary>
            <pre>{{ row }}</pre>
          </details>
        </template>
      </div>
      <footer>
        <span class="muted">可保持小窗打开，继续填写后方上架资料</span>
        <button class="btn small" @click="reset()">恢复默认大小</button>
        <a
          v-if="product"
          :href="
            (product.source === 'GigaB2B' ? '#/giga-product/' : '#/product/') +
            encodeURIComponent(product.id)
          "
          target="_blank"
          rel="noopener"
          >在新页打开完整档案 ↗</a
        >
      </footer>
    </dialog>
  </Teleport>
</template>
<style scoped>
.product-info-dialog {
  position: fixed;
  inset: 0;
  margin: auto;
  z-index: 80;
  width: min(960px, 94vw);
  height: fit-content;
  container-type: inline-size;
  max-height: 90vh;
  padding: 0;
  border: 1px solid #dce5ed;
  border-radius: 18px;
  color: #253d50;
  background: #fff;
  box-shadow: 0 16px 54px #28435c24;
  overflow: hidden;
}
.product-info-dialog[open] {
  display: flex;
  flex-direction: column;
}
header {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 20px;
  padding: 22px 26px 16px;
  flex-shrink: 0;
  background: #f5f8fb;
  cursor: grab;
  touch-action: none;
  user-select: none;
}
header.dragging {
  cursor: grabbing;
}
.drag-hint {
  display: inline-block;
  margin-left: 14px;
  color: #75889a;
  font-size: 12px;
}
.product-info-close {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  padding: 0;
  flex-shrink: 0;
  border: 1px solid #cbd9e5;
  border-radius: 12px;
  background: #e4edf5;
  color: #38546d;
  cursor: pointer;
  transition:
    background 150ms ease,
    border-color 150ms ease;
}
.product-info-close:hover {
  background: #d6e4f0;
  border-color: #adc4d7;
}
.product-info-close:focus-visible {
  outline: 2px solid #7597b5;
  outline-offset: 3px;
}
.product-info-close :deep(svg) {
  width: 18px;
  height: 18px;
}
h2 {
  font-size: 20px;
  line-height: 1.5;
  margin: 6px 0 0;
  max-height: 90px;
  overflow: auto;
  overflow-wrap: anywhere;
}
.product-info-tabs {
  display: flex;
  gap: 8px;
  padding: 12px 26px;
  border-bottom: 1px solid #e4ebf2;
}
.product-info-tabs button {
  padding: 9px 16px;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 8px;
  color: #64758a;
  cursor: pointer;
  font: inherit;
}
.product-info-tabs button[aria-pressed='true'] {
  background: #e8f0f7;
  border-color: #c5d7e6;
  color: #284e70;
  font-weight: 600;
}
.product-info-body {
  padding: 8px 26px 24px;
  overflow-y: auto;
  overscroll-behavior: contain;
  min-height: 0;
}
h3 {
  margin: 24px 0 12px;
  font-size: 17px;
}
.product-info-facts {
  display: grid;
  grid-template-columns: 145px minmax(0, 1fr);
  gap: 12px;
}
dd {
  margin: 0;
  overflow-wrap: anywhere;
}
li {
  margin-bottom: 8px;
  line-height: 1.7;
}
.description,
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.product-info-images {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 12px;
  margin: 16px 0;
}
.product-info-images :deep(img) {
  width: 100%;
  height: 140px;
  object-fit: contain;
  border: 1px solid #e8edf3;
  border-radius: 10px;
}
footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 26px;
  border-top: 1px solid #e4ebf2;
  flex-shrink: 0;
}
@container (max-width: 600px) {
  header,
  .product-info-tabs,
  .product-info-body,
  footer {
    padding-left: 16px;
    padding-right: 16px;
  }
  .product-info-facts {
    grid-template-columns: 1fr;
    gap: 6px;
  }
  dd {
    margin-bottom: 10px;
  }
}
/* Breakpoints follow this window, even on a wide desktop. */
@container (max-width: 850px) {
  :deep(.giga-overview),
  :deep(.giga-source .giga-field-grid),
  :deep(.organized-grid) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  :deep(.giga-total) {
    grid-column: 1 / -1;
  }
  :deep(.giga-two-col) {
    grid-template-columns: minmax(0, 1fr);
  }
}
@container (max-width: 600px) {
  :deep(.giga-overview),
  :deep(.giga-source .giga-field-grid),
  :deep(.organized-grid),
  :deep(.giga-metrics) {
    grid-template-columns: minmax(0, 1fr);
  }
  :deep(.giga-panel),
  :deep(.giga-stat),
  :deep(.giga-total) {
    padding: 14px;
  }
  .product-info-tabs {
    flex-wrap: wrap;
    gap: 4px;
  }
  .drag-hint {
    display: block;
    margin-left: 0;
  }
  footer {
    gap: 8px;
  }
}
.product-info-body {
  min-width: 0;
  flex: 1 1 auto;
}
header > div {
  min-width: 0;
}
.product-info-dialog.resizing {
  user-select: none;
}
.resize-handle {
  position: absolute;
  z-index: 5;
  touch-action: none;
}
.resize-n,
.resize-s {
  left: 14px;
  right: 14px;
  height: 8px;
  cursor: ns-resize;
}
.resize-n {
  top: 0;
}
.resize-s {
  bottom: 0;
}
.resize-e,
.resize-w {
  top: 14px;
  bottom: 14px;
  width: 8px;
  cursor: ew-resize;
}
.resize-e {
  right: 0;
}
.resize-w {
  left: 0;
}
.resize-ne,
.resize-nw,
.resize-se,
.resize-sw {
  width: 14px;
  height: 14px;
}
.resize-ne,
.resize-sw {
  cursor: nesw-resize;
}
.resize-nw,
.resize-se {
  cursor: nwse-resize;
}
.resize-ne {
  top: 0;
  right: 0;
}
.resize-nw {
  top: 0;
  left: 0;
}
.resize-se {
  bottom: 0;
  right: 0;
  border-right: 2px solid #9aafc1;
  border-bottom: 2px solid #9aafc1;
  border-radius: 0 0 10px;
}
.resize-sw {
  bottom: 0;
  left: 0;
}
</style>
