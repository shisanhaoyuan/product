<script setup lang="ts">
import type { StoreHistory } from '../api/types';
defineProps<{ entries?: StoreHistory[] }>();
const labels: Record<string, string> = {
  draft: '预上架',
  processing: '处理中',
  sending: '提交中',
  unknown: '待确认',
  published: '已发布',
  success: '已处理',
  failed: '失败',
  rejected: '审核失败',
  unpublished: '未发布',
  stock_pending: '库存待同步',
};
</script>
<template>
  <div class="store-history" @click.stop>
    <strong>店铺上架记录</strong>
    <span v-if="!entries?.length" class="muted">暂无记录</span>
    <details v-else>
      <summary>{{ [...new Set(entries.map((e) => e.store_name))].join('、') }}</summary>
      <div v-for="entry in entries" :key="entry.batch_id" class="store-history-entry">
        <b>{{ entry.store_name }}</b>
        <span
          v-for="(count, status) in entry.counts"
          :key="status"
          :class="{
            'seller-risk-label': ['failed', 'rejected', 'unpublished'].includes(String(status)),
          }"
        >
          {{ labels[status] || '待确认' }} {{ count }} 个 SKU
        </span>
      </div>
    </details>
  </div>
</template>
<style scoped>
.store-history {
  display: grid;
  gap: 6px;
  font-size: 13px;
  margin: 10px 0;
  color: #536b7e;
}
.store-history summary {
  cursor: pointer;
  white-space: normal;
}
.store-history-entry {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 0;
  border-bottom: 1px solid #e3e9ef;
}
</style>
