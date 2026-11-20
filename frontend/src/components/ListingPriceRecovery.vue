<script setup lang="ts">
import { computed, ref } from 'vue';

const props = defineProps<{
  asin?: string;
  multiplier: string;
  collection?: { state: string; message?: string };
  disabled: boolean;
}>();
const emit = defineEmits<{ confirm: [originalPrice: string] }>();
const originalPrice = ref('');
const input = ref<HTMLInputElement>();
const valid = computed(
  () =>
    /^\d+(?:\.\d{1,2})?$/.test(originalPrice.value.trim()) &&
    Number(originalPrice.value) > 0 &&
    Number(originalPrice.value) <= 10000000,
);
const sourceUrl = computed(() =>
  /^[A-Z0-9]{10}$/.test(props.asin || '')
    ? `https://www.amazon.com/dp/${props.asin}?th=1&psc=1`
    : '',
);
const reason = computed(() => {
  if (props.collection?.state === 'verification_required')
    return (
      props.collection.message ||
      'Amazon 人机验证拦截了后台采集，当前子商品尚未取得可用于计算的来源原价。'
    );
  return props.collection?.message || '当前子商品缺少可核验的来源原价，无法自动计算售价。';
});
function focus() {
  input.value?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  input.value?.focus({ preventScroll: true });
}
defineExpose({ focus });
</script>

<template>
  <section class="price-recovery" aria-label="补齐来源价格">
    <div class="price-recovery-heading">
      <strong>来源价格待确认</strong>
      <a v-if="sourceUrl" :href="sourceUrl" target="_blank" rel="noopener noreferrer"
        >查看来源商品 ↗</a
      >
    </div>
    <p>{{ reason }}</p>
    <p>
      填写你在来源网页核对过的原价，系统按本批次倍率计算售价并记录人工确认。也可直接填写下方售价后点击“已验证”。
    </p>
    <div class="price-recovery-inputs">
      <label>
        <span>来源原价（USD）</span>
        <input
          ref="input"
          v-model="originalPrice"
          type="text"
          inputmode="decimal"
          placeholder="例如 72.89"
          :disabled="disabled"
          @keydown.enter.prevent="valid && !disabled && emit('confirm', originalPrice.trim())"
        />
      </label>
      <span class="price-recovery-formula">原价 × {{ multiplier }} = 上架售价</span>
      <button
        class="btn primary"
        :disabled="disabled || !valid"
        @click="emit('confirm', originalPrice.trim())"
      >
        计算售价并确认
      </button>
    </div>
    <small>仅更新当前子商品的刊登价格；确认后继续校验其他信息，不会自动上架。</small>
  </section>
</template>

<style scoped>
.price-recovery {
  margin: 16px 0;
  padding: 20px;
  border: 1px solid #c4d5e3;
  border-radius: 14px;
  background: #eef4f9;
  color: #34536c;
}
.price-recovery-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.price-recovery-heading strong {
  font-size: 17px;
}
.price-recovery-heading a {
  color: #365f80;
  font-weight: 600;
}
.price-recovery p {
  margin: 10px 0;
  line-height: 1.65;
}
.price-recovery-inputs {
  display: flex;
  align-items: end;
  flex-wrap: wrap;
  gap: 14px;
  margin: 16px 0;
}
.price-recovery-inputs label {
  display: grid;
  gap: 8px;
  flex: 1 1 200px;
  max-width: 300px;
}
.price-recovery-inputs input {
  width: 100%;
  background: #fff;
  min-height: 44px;
}
.price-recovery-formula {
  align-self: center;
  font-weight: 600;
}
.price-recovery small {
  line-height: 1.6;
  color: #5b7186;
}
@media (max-width: 680px) {
  .price-recovery-inputs {
    align-items: stretch;
    flex-direction: column;
  }
  .price-recovery-inputs label {
    flex-basis: auto;
    max-width: none;
  }
}
</style>
