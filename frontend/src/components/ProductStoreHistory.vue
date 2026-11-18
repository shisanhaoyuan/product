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
    <strong>店铺处理记录（含预上架）</strong>
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
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 36px;
  font-size: 13px;
  margin: 0 0 12px;
  padding: 8px 12px;
  border: 1px solid #dce6ee;
  border-radius: 10px;
  background: #f5f8fb;
  color: #536b7e;
}
.store-history > strong {
  color: #46647b;
  font-size: 12px;
  white-space: nowrap;
}
.store-history > .muted {
  color: #8092a1;
}
.store-history summary {
  cursor: pointer;
  color: #385b75;
  font-weight: 600;
}
.store-history details {
  min-width: 0;
}
.store-history-entry {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
  padding: 8px 10px;
  border: 1px solid #e3e9ef;
  border-radius: 8px;
  background: #fff;
}
.store-history-entry b {
  color: #385b75;
}
@media (max-width: 760px) {
  .store-history {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }
}
</style>
