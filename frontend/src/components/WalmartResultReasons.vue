<script setup lang="ts">
import type { ListingResult } from '../api/publishing';
defineProps<{ errors: ListingResult['errors'] }>();
function explanation(reason: ListingResult['errors'][number]) {
  // Existing saved attempts retain old summaries; explain their original response too.
  if (/\b(?:main\s*image(?:\s*url)?|primary\s+image)\b/i.test(reason.original))
    return '沃尔玛未取得可用主图。请检查主图链接能否公开访问、图片格式是否有效，处理后重新校验提交。';
  return reason.message || '平台返回以下问题，请按具体原因处理。';
}
</script>
<template>
  <div v-for="(reason, i) in errors" :key="i" class="walmart-reason">
    <strong>{{ explanation(reason) }}</strong>
    <p v-if="reason.original"><span>平台详情：</span>{{ reason.original }}</p>
    <small v-if="reason.code">错误代码：{{ reason.code }}</small>
  </div>
</template>
<style scoped>
.walmart-reason {
  margin: 10px 0;
  padding: 12px 14px;
  border-left: 3px solid #dc7070;
  border-radius: 8px;
  background: #fff5f5;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
strong {
  color: #a33131;
  font-weight: 600;
}
p {
  color: #475569;
  line-height: 1.6;
  margin: 8px 0 0;
}
small {
  display: block;
  margin-top: 6px;
  color: #64748b;
}
</style>
