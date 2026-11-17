<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { catalogApi } from '../api/client';
import type { ProductDetail } from '../api/types';
import type { ListingRow } from '../api/publishing';
import { errorMessage, plain } from '../utils/display';
import ProductImage from './ProductImage.vue';
import ProductStoreHistory from './ProductStoreHistory.vue';
import AppIcon from './AppIcon.vue';
const props = defineProps<{ listingId: string; row?: ListingRow }>();
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLDialogElement>();
const product = ref<ProductDetail>();
const error = ref('');
const sourceVariant = computed(() =>
  props.row ? product.value?.variants.find((v) => v.sku === props.row?.source_sku) : undefined,
);
const images = computed(() => sourceVariant.value?.images || product.value?.images || []);
watch(
  () => props.listingId,
  async (id) => {
    error.value = '';
    product.value = undefined;
    try {
      const result = await catalogApi.product(id);
      if (id === props.listingId) product.value = result;
    } catch (e) {
      error.value = errorMessage(e);
    }
  },
  { immediate: true },
);
const open = (el: unknown) => {
  dialog.value = el as HTMLDialogElement;
  dialog.value?.showModal();
};
</script>
<template>
  <dialog
    :ref="open"
    class="product-info-dialog"
    @close="emit('close')"
    @click="$event.target === dialog && dialog?.close()"
  >
    <header>
      <div>
        <span class="muted">商品信息详情</span>
        <h2>{{ plain(String(row?.visible.productName || product?.copy.title || '正在读取…')) }}</h2>
      </div>
      <button class="icon-btn" aria-label="关闭商品详情" @click="dialog?.close()">
        <AppIcon name="X" />
      </button>
    </header>
    <p v-if="error" class="error-box">{{ error }}</p>
    <template v-if="product">
      <dl>
        <dt>来源 SKU / ASIN</dt>
        <dd>{{ row?.source_sku || product.sku }} / {{ product.asin || '—' }}</dd>
        <template v-if="row"
          ><dt>店铺 SKU</dt>
          <dd>{{ row.platform_sku }}</dd>
          <dt>商品类型</dt>
          <dd>{{ row.product_type || '未选择' }}</dd>
          <dt>售价 / 编码</dt>
          <dd>{{ row.orderable.price }} / {{ row.identification.value || '未填写' }}</dd></template
        >
        <dt>来源 / 卖家</dt>
        <dd>{{ product.source }} / {{ product.seller || '未采集' }}</dd>
      </dl>
      <ProductStoreHistory :entries="product.store_history" />
      <p class="muted">
        {{ row ? '以下为本批次当前刊登资料，保存后生效。' : '以下为商品库已保存资料。' }}
      </p>
      <h3>商品卖点</h3>
      <ol>
        <li
          v-for="(feature, i) in (row?.visible.keyFeatures as string[]) || product.copy.features"
          :key="i"
        >
          {{ plain(feature) }}
        </li>
      </ol>
      <h3>商品描述</h3>
      <div class="description">
        {{ plain(String(row?.visible.shortDescription ?? product.copy.description)) || '未填写' }}
      </div>
      <details v-if="row">
        <summary>本批次全部属性与履约信息</summary>
        <pre>{{ { visible: row.visible, orderable: row.orderable, extra: row.extra } }}</pre>
      </details>
      <h3>来源图片</h3>
      <div class="product-info-images">
        <ProductImage v-for="url in images" :key="url" :src="url" />
      </div>
      <a
        :href="(product.source === 'GigaB2B' ? '#/giga-product/' : '#/product/') + product.id"
        target="_blank"
        rel="noopener"
        >打开完整商品档案 ↗</a
      >
    </template>
  </dialog>
</template>
<style scoped>
.product-info-dialog {
  width: min(960px, 92vw);
  max-height: 90vh;
  border: 1px solid #dce5ed;
  border-radius: 18px;
  padding: 26px;
  color: #253d50;
}
.product-info-dialog::backdrop {
  background: #22384b66;
}
header {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 20px;
}
h2 {
  font-size: 22px;
  line-height: 1.5;
}
dl {
  display: grid;
  grid-template-columns: 140px 1fr;
  gap: 12px;
}
dd {
  margin: 0;
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
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
