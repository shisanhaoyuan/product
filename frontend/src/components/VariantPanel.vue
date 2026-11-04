<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { Variant } from '../api/types';
import { money, plain, safeUrl, text } from '../utils/display';
import CopyContent from './CopyContent.vue';
import ProductImage from './ProductImage.vue';
const props = defineProps<{ variants: Variant[] }>();
const selectedSku = ref('');
watch(
  () => props.variants,
  (variants) => {
    selectedSku.value = variants[0]?.sku || '';
  },
  { immediate: true },
);
const selected = computed(() =>
  props.variants.find((variant) => variant.sku === selectedSku.value),
);
</script>
<template>
  <div v-if="variants.length" class="table-scroll">
    <table class="data-table">
      <thead>
        <tr>
          <th>图片</th>
          <th>平台 SKU / ASIN</th>
          <th>属性</th>
          <th>售价 USD</th>
          <th>库存</th>
          <th>EAN</th>
          <th>文案</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="variant in variants"
          :key="variant.sku"
          class="variant-row"
          :class="{ selected: selectedSku === variant.sku }"
          :data-variant="variant.sku"
          tabindex="0"
          :aria-selected="selectedSku === variant.sku"
          @click="selectedSku = variant.sku"
          @keydown.enter="selectedSku = variant.sku"
          @keydown.space.prevent="selectedSku = variant.sku"
        >
          <td><ProductImage :src="variant.images[0]" image-class="table-thumb" /></td>
          <td class="mono">
            {{ variant.sku }}<br /><span class="muted">{{
              variant.asin || 'ASIN 关联未记录'
            }}</span>
          </td>
          <td>
            <div v-for="(value, key) in variant.attributes" :key="key">
              {{ key }}: {{ text(value) }}
            </div>
          </td>
          <td>
            <span class="price">{{ money(variant.price) }}</span
            ><span
              v-if="variant.price_check.status"
              class="check-state"
              :class="{ bad: variant.price_check.status !== 'verified' }"
              >{{
                variant.price_check.status === 'verified' ? '✓ 历史核验通过' : '△ 核验异常'
              }}</span
            >
          </td>
          <td>
            {{ variant.stock ?? '—'
            }}<span
              v-if="variant.stock_check.status"
              class="check-state"
              :class="{ bad: variant.stock_check.status !== 'verified' }"
              >{{
                variant.stock_check.status === 'verified' ? '✓ 历史核验通过' : '△ 核验异常'
              }}</span
            >
          </td>
          <td class="mono">{{ variant.ean || '空' }}</td>
          <td>
            {{ variant.copy.features.filter((value) => plain(value)).length }} 条卖点<br /><span
              class="muted"
              >{{ plain(variant.copy.description) ? '有描述' : '描述为空' }}</span
            >
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  <div v-else class="empty">当前快照未记录子变体行；可用价格与库存在商品档案及填写属性中查看。</div>
  <div id="variant-detail">
    <div v-if="selected" :key="selected.sku" class="variant-detail">
      <div class="variant-detail-head">
        <ProductImage :src="selected.images[0]" image-class="table-thumb" />
        <div>
          <p class="eyebrow">SELECTED VARIANT / {{ selected.sku }}</p>
          <h3>{{ selected.copy.title || '本行未记录标题' }}</h3>
          <span class="subtitle"
            >ASIN：{{ selected.asin || '未记录' }}　卖家：{{ selected.seller || '未采集' }}　<a
              v-if="safeUrl(selected.source_url)"
              class="detail-link"
              :href="safeUrl(selected.source_url)"
              target="_blank"
              rel="noopener noreferrer"
              >查看本变体来源 ↗</a
            ></span
          >
        </div>
      </div>
      <div v-if="selected.identity_note" class="notice warning notice-space">
        {{ selected.identity_note }}
      </div>
      <CopyContent :copy="selected.copy" />
      <details class="details">
        <summary>本子 SKU 来源、价格与库存核验</summary>
        <pre class="json-block">{{
          text({
            source_facts: selected.source_facts,
            price_check: selected.price_check,
            stock_check: selected.stock_check,
          })
        }}</pre>
      </details>
      <details class="details">
        <summary>原始表格单元格（按 SKU 保存）</summary>
        <pre class="json-block">{{ text(selected.raw_cells) }}</pre>
      </details>
    </div>
  </div>
</template>
