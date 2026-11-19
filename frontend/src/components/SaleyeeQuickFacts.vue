<script setup lang="ts">
import type { SaleyeeSummary } from '../api/types';
import { money } from '../utils/display';
defineProps<{ summary?: SaleyeeSummary | null }>();
</script>
<template>
  <div v-if="summary" class="saleyee-quick">
    <span :class="{ empty: summary.inventory === 0 }"
      >库存 {{ summary.inventory ?? '待选仓库' }}</span
    ><span>不另加运费</span
    ><span>采购价 {{ summary.landed === null ? '待确认' : money(summary.landed) }}</span>
  </div>
</template>
<style scoped>
.saleyee-quick {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin: 8px 0;
  font-size: 12px;
}
.saleyee-quick span {
  padding: 4px 7px;
  background: #edf3f7;
  border-radius: 6px;
  color: #45617b;
}
.saleyee-quick .empty {
  color: #bf4141;
  background: #fff0ef;
}
</style>
