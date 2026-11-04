<script setup lang="ts">
import { computed } from 'vue';
import type { ProductDetail } from '../api/types';
import { useAuth } from '../composables/useAuth';
const props = defineProps<{ product: ProductDetail }>();
const { auth } = useAuth();
const canEdit = computed(
  () => !auth.enabled || ['admin', 'editor'].includes(auth.user?.role || ''),
);
const sellers = computed(() => {
  const rows = new Map<string, string[]>();
  const add = (name: string, label: string) => {
    if (!name.trim()) return;
    const labels = rows.get(name) || [];
    labels.push(label);
    rows.set(name, labels);
  };
  add(props.product.seller, '主商品');
  props.product.variants.forEach((v) => add(v.seller, v.sku));
  return [...rows].map(([name, skus]) => ({ name, skus }));
});
</script>
<template>
  <section class="section seller-section">
    <div class="section-head">
      <div>
        <h2>卖家风险</h2>
        <p>按来源平台和卖家名称匹配，覆盖主商品与子变体。</p>
      </div>
      <RouterLink class="btn small" to="/blacklist">查看黑名单</RouterLink>
    </div>
    <div class="section-body">
      <div v-for="entry in product.seller_blacklist" :key="entry.id" class="seller-risk-note">
        <strong>已拉黑 · {{ entry.source }} / {{ entry.seller }}</strong>
        <p class="blacklist-reason">{{ entry.reason }}</p>
        <RouterLink
          v-if="canEdit"
          class="detail-link"
          :to="{ path: '/blacklist', query: { q: entry.seller } }"
          >修改原因 / 解除拉黑 ↗</RouterLink
        >
      </div>
      <p v-if="!product.seller_blacklist.length" class="subtitle">
        未命中黑名单；未采集的卖家无法判断。
      </p>
      <div v-for="seller in sellers" :key="seller.name" class="seller-row">
        <div>
          <strong>{{ seller.name }}</strong>
          <p class="subtitle">{{ seller.skus.join(' · ') }}</p>
        </div>
        <RouterLink
          v-if="canEdit && product.source.trim()"
          class="btn small"
          :to="{ path: '/blacklist', query: { source: product.source, seller: seller.name } }"
          >管理 / 拉黑</RouterLink
        >
      </div>
      <p v-if="!sellers.length" class="subtitle">
        尚未采集卖家。可在黑名单页面手动添加已确认的卖家。
      </p>
    </div>
  </section>
</template>
