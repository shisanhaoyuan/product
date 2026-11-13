<script setup lang="ts">
import type { ReviewIssue } from '../utils/reviewIssues';
defineProps<{ issues: ReviewIssue[]; disabled?: boolean }>();
defineEmits<{ locate: [issue: ReviewIssue] }>();
</script>
<template>
  <div class="review-issues">
    <article v-for="(issue, index) in issues" :key="index" class="review-issue-card">
      <button class="review-issue-link" :disabled="disabled" @click="$emit('locate', issue)">
        <span class="review-issue-number">{{ index + 1 }}</span>
        <span class="review-issue-content"
          ><strong>{{ issue.title }}</strong
          ><span>{{ issue.message }}</span
          ><small>{{ issue.location }}</small></span
        >
        <span class="review-issue-arrow">定位 ↗</span>
      </button>
      <details class="details review-original">
        <summary>原始检查记录</summary>
        <pre class="json-block">{{ JSON.stringify(issue.raw, null, 2) }}</pre>
      </details>
    </article>
  </div>
</template>
<style scoped>
.review-issues {
  display: grid;
  gap: 12px;
  margin-top: 18px;
}
.review-issue-card {
  border: 1px solid #e4dfd4;
  border-radius: 10px;
  background: #fffdf8;
  overflow: hidden;
}
.review-issue-link {
  display: flex;
  width: 100%;
  padding: 18px;
  gap: 14px;
  text-align: left;
  background: transparent;
  border: 0;
  color: #38485d;
  cursor: pointer;
}
.review-issue-link:hover {
  background: #f8f5ec;
}
.review-issue-link:disabled {
  opacity: 0.6;
  cursor: default;
}
.review-issue-number {
  font-size: 12px;
  color: #90784c;
  background: #f1e9d8;
  width: 24px;
  height: 24px;
  border-radius: 6px;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}
.review-issue-content {
  display: grid;
  gap: 7px;
  min-width: 0;
  flex: 1;
  line-height: 1.65;
}
.review-issue-content strong {
  font-size: 14px;
}
.review-issue-content span {
  font-size: 13px;
  overflow-wrap: anywhere;
}
.review-issue-content small {
  font-size: 11px;
  color: #7c8998;
  overflow-wrap: anywhere;
}
.review-issue-arrow {
  color: #6582a4;
  font-size: 12px;
  white-space: nowrap;
}
.review-original {
  margin: 0 18px 14px 56px;
  font-size: 11px;
}
</style>
