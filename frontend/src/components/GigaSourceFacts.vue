<script setup lang="ts">
import { computed } from 'vue';
import { text } from '../utils/display';
const props = defineProps<{ facts: Record<string, unknown> }>();
function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
const giga = computed(() => object(props.facts.giga));
const price = computed(() => object(giga.value.price));
const inventory = computed(() => object(giga.value.inventory));
const detail = computed(() => object(giga.value.detail));
const rows = computed(() => [
  ['供应商 SKU', props.facts.sku],
  ['币种', price.value.currency],
  ['商品原价', price.value.price],
  ['专享价', price.value.exclusivePrice],
  ['活动折扣价', price.value.discountedPrice],
  ['普通地区运费（含打包）', price.value.shippingFee],
  ['偏远地区运费', price.value.remoteShippingFee],
  ['Seller 可售库存', object(inventory.value.sellerInventoryInfo).sellerAvailableInventory],
  ['Buyer 自有可用库存', object(inventory.value.buyerInventoryInfo).totalBuyerAvailableInventory],
  ['来源类目', detail.value.categoryPath || detail.value.category],
  [
    '可购买',
    detail.value.skuAvailable === true
      ? '是'
      : detail.value.skuAvailable === false
        ? '否'
        : '未返回',
  ],
]);
</script>
<template>
  <section class="giga-source">
    <h3>大建云仓 · 最新来源资料</h3>
    <p class="subtitle">以下为接口原始值。商品库手工编辑值单独保留，不覆盖本区来源事实。</p>
    <div class="identity-grid">
      <div v-for="[label, value] in rows" :key="text(label)" class="identity-item">
        <label>{{ label }}</label>
        <div class="value">{{ value == null || value === '' ? '未返回' : value }}</div>
      </div>
    </div>
    <p class="notice">
      库存为平台可采购数量；不包含 Buyer 自有、共享及在途库存。来源商品未完成目标店铺上架核验。
    </p>
    <details class="details">
      <summary>展开原始 JSON（含仓库分布、价格区间及组合信息）</summary>
      <pre class="json-block">{{ text(giga) }}</pre>
    </details>
  </section>
</template>
<style scoped>
.giga-source {
  display: grid;
  gap: 16px;
  margin-top: 24px;
}
.giga-source h3,
.giga-source p {
  margin: 0;
}
</style>
