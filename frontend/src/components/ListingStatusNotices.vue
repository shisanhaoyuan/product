<script setup lang="ts">
import { computed } from 'vue';
import type { ListingDocument, ListingResult } from '../api/publishing';
import { money } from '../utils/display';
import AppIcon from './AppIcon.vue';

const props = defineProps<{
  review?: NonNullable<ListingDocument['source_price_reviews']>[string];
  result?: ListingResult;
}>();
const verifiedAt = computed(() => {
  if (!props.review?.at) return '时间未记录';
  const date = new Date(props.review.at);
  if (!Number.isFinite(date.getTime())) return '时间未记录';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
});
</script>

<template>
  <div v-if="review || result" class="listing-status-notices">
    <section
      v-if="review"
      class="listing-status-card price-confirmation"
      role="status"
      aria-label="来源价格人工验证记录"
    >
      <div class="status-card-heading">
        <span class="status-card-title"><AppIcon name="ShieldCheck" />来源价格已人工验证</span>
        <span class="status-card-time">{{ verifiedAt }} · 北京时间</span>
      </div>
      <dl class="confirmed-price-facts">
        <div v-if="review.original_price">
          <dt>人工原价</dt>
          <dd>{{ money(review.original_price) }}</dd>
        </div>
        <div v-if="review.original_price && review.multiplier">
          <dt>售价倍率</dt>
          <dd>× {{ review.multiplier }}</dd>
        </div>
        <div class="confirmed-selling-price">
          <dt>确认售价</dt>
          <dd>{{ money(review.price) }}</dd>
        </div>
      </dl>
      <p class="status-card-note">修改售价或来源后，需要重新确认。</p>
    </section>
    <section
      v-if="result"
      class="listing-status-card submission-status"
      aria-label="沃尔玛处理状态"
    >
      <span class="status-card-title"><AppIcon name="History" />沃尔玛处理状态</span>
      <p class="status-card-message">{{ result.message }}</p>
      <p class="status-card-note">当前状态下，暂不支持修改资料或重复提交。</p>
    </section>
  </div>
</template>

<style scoped>
.listing-status-notices {
  display: grid;
  gap: 12px;
  margin: 18px 30px;
  min-width: 0;
}
.listing-status-card {
  min-width: 0;
  padding: 18px 20px;
  border: 1px solid #d8e3ed;
  border-radius: 14px;
  background: #f1f6fa;
  color: #34536b;
}
.status-card-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px 20px;
}
.status-card-title {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  font-size: 15px;
  font-weight: 650;
  line-height: 1.5;
}
.status-card-title .icon {
  width: 19px;
  height: 19px;
  flex: 0 0 19px;
  color: #62839e;
}
.status-card-time {
  font-size: 12px;
  color: #73869a;
  line-height: 1.6;
}
.confirmed-price-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: 14px 0 12px;
}
.confirmed-price-facts > div {
  display: grid;
  gap: 5px;
  min-width: 120px;
  padding: 10px 14px;
  border: 1px solid #dde6ee;
  border-radius: 10px;
  background: #ffffffad;
}
.confirmed-price-facts dt {
  margin: 0;
  color: #6c8296;
  font-size: 12px;
}
.confirmed-price-facts dd {
  margin: 0;
  font-size: 18px;
  font-weight: 650;
  line-height: 1.4;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.confirmed-price-facts .confirmed-selling-price {
  background: #e0ecf5;
  border-color: #c4d8e8;
  color: #2c5778;
}
.status-card-note {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: #728498;
}
.submission-status {
  background: #f7f9fc;
}
.status-card-message {
  margin: 9px 0 6px;
  font-size: 14px;
  line-height: 1.75;
  overflow-wrap: anywhere;
}
@media (max-width: 680px) {
  .listing-status-notices {
    margin: 14px;
  }
  .listing-status-card {
    padding: 16px;
  }
  .confirmed-price-facts {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
  .confirmed-price-facts > div {
    min-width: 0;
  }
  .confirmed-selling-price {
    grid-column: 1 / -1;
  }
}
</style>
