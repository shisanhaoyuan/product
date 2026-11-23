<script setup lang="ts">
import { computed } from 'vue';
import type { ProductSummary } from '../api/types';
import type { VariantPreviewState } from '../composables/useVariantPreview';
import { money } from '../utils/display';
import { previewVariantAttributes } from '../utils/variantPreview';
import ProductImage from './ProductImage.vue';

const props = defineProps<{ product: ProductSummary; state: VariantPreviewState }>();
defineEmits<{ retry: [] }>();
const variants = computed(() =>
  (props.state.variants ?? []).map((variant) => ({
    ...variant,
    previewAttributes: previewVariantAttributes(variant.attributes),
  })),
);
</script>

<template>
  <section
    class="variant-preview"
    :aria-label="product.sku + ' 的子变体'"
    :aria-busy="state.loading"
  >
    <p v-if="state.loading" class="variant-preview-message" role="status">正在加载子变体…</p>
    <div v-else-if="state.error" class="variant-preview-message" role="alert">
      <span>子变体加载失败：{{ state.error }}</span>
      <button class="btn small" @click="$emit('retry')">重新加载</button>
    </div>
    <template v-else-if="state.variants?.length">
      <header class="variant-preview-heading">
        <strong>子变体 · {{ state.variants.length }}</strong>
        <span>图片 / SKU / 规格 / 售价 / 库存</span>
      </header>
      <ul class="variant-preview-list">
        <li v-for="variant in variants" :key="variant.sku" class="variant-preview-item">
          <div class="variant-preview-image">
            <ProductImage
              :listing-id="product.id"
              :src="variant.images[0]"
              :fallbacks="variant.images.slice(1)"
              :alt="variant.copy.title || variant.sku"
            />
          </div>
          <div class="variant-preview-info">
            <strong class="mono">{{ variant.sku }}</strong>
            <span v-if="variant.asin" class="variant-preview-asin mono"
              >ASIN · {{ variant.asin }}</span
            >
            <div v-if="variant.previewAttributes.length" class="variant-preview-attributes">
              <span v-for="attribute in variant.previewAttributes" :key="attribute.label">
                <span class="muted">{{ attribute.label }}：</span>{{ attribute.value }}
              </span>
            </div>
            <span v-else class="muted">未记录变体规格</span>
          </div>
          <div class="variant-preview-numbers">
            <strong>{{ money(variant.price, product.currency ?? 'USD') }}</strong>
            <span
              :class="{
                'variant-preview-no-stock': variant.stock?.trim() && Number(variant.stock) === 0,
              }"
            >
              库存 · {{ variant.stock?.trim() || '未记录' }}
            </span>
          </div>
        </li>
      </ul>
    </template>
    <p v-else class="variant-preview-message">暂无子变体记录。</p>
  </section>
</template>

<style scoped>
.variant-preview {
  min-width: 0;
  color: var(--ink);
  font-size: 13px;
  white-space: normal;
}
.variant-preview-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 6px 16px;
  padding: 12px 14px;
  color: #496b95;
}
.variant-preview-heading span {
  color: var(--muted);
  font-size: 12px;
}
.variant-preview-list {
  max-height: 440px;
  overflow-y: auto;
  overscroll-behavior: contain;
  margin: 0;
  padding: 0 12px 12px;
  list-style: none;
}
.variant-preview-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 14px;
  padding: 12px;
  border: 1px solid #dfe8f3;
  border-radius: 10px;
  background: #fff;
}
.variant-preview-item + .variant-preview-item {
  margin-top: 8px;
}
.variant-preview-image {
  display: grid;
  place-items: center;
  flex: 0 0 56px;
  width: 56px;
  height: 64px;
  overflow: hidden;
  border-radius: 8px;
  background: #f5f7fa;
}
.variant-preview-image :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.variant-preview-image :deep(.image-placeholder) {
  font-size: 10px;
}
.variant-preview-image :deep(.image-placeholder > span) {
  margin-bottom: 0;
}
.variant-preview-info {
  display: grid;
  flex: 1 1 130px;
  min-width: 0;
  gap: 5px;
  overflow-wrap: anywhere;
}
.variant-preview-asin {
  color: var(--muted);
  font-size: 12px;
}
.variant-preview-attributes {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  line-height: 1.6;
}
.variant-preview-numbers {
  display: grid;
  gap: 6px;
  margin-left: auto;
  color: var(--muted);
}
.variant-preview-numbers strong {
  color: #3b648a;
  font-size: 15px;
}
.variant-preview-no-stock {
  color: #b33b42;
}
.variant-preview-message {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 0;
  padding: 16px;
  overflow-wrap: anywhere;
}
</style>
