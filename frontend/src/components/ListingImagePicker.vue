<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { catalogApi } from '../api/client';
import { errorMessage } from '../utils/display';
import ProductImage from './ProductImage.vue';
import AppIcon from './AppIcon.vue';
const props = defineProps<{
  listingId: string;
  sku: string;
  existing: string[];
  sourceImages: string[];
}>();
const emit = defineEmits<{ close: []; add: [urls: string[]] }>();
const dialog = ref<HTMLDialogElement>();
const candidates = ref<string[]>([...new Set(props.sourceImages)]);
const selected = ref<string[]>([]);
const loading = ref(true),
  error = ref('');
const remaining = computed(() => Math.max(0, 8 - props.existing.length));
const available = computed(() => [...new Set(candidates.value)].filter(Boolean));
function apply() {
  emit(
    'add',
    selected.value.filter((url) => !props.existing.includes(url)).slice(0, remaining.value),
  );
  emit('close');
}
onMounted(async () => {
  dialog.value?.showModal();
  try {
    const product = await catalogApi.product(props.listingId);
    const matches = product.variants.filter((v) => v.sku === props.sku);
    const record = product.variants.length
      ? matches.length === 1
        ? matches[0]
        : undefined
      : product.sku === props.sku
        ? product
        : undefined;
    candidates.value = [...new Set([...candidates.value, ...(record?.images || [])])];
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    loading.value = false;
  }
});
</script>
<template>
  <dialog ref="dialog" class="listing-picker" aria-label="选择商品附图" @close="emit('close')">
    <header>
      <div>
        <h3>选择商品附图</h3>
        <p>{{ sku }} · 已选 {{ selected.length }} 张，还可添加 {{ remaining }} 张</p>
      </div>
      <button class="icon-btn" aria-label="关闭图片选择" @click="emit('close')">
        <AppIcon name="X" />
      </button>
    </header>
    <p v-if="loading" class="muted">正在读取商品图片…</p>
    <p v-if="error" class="error-box">{{ error }}；可选择已有来源图片，或关闭后本地上传。</p>
    <p v-if="!loading && !available.length" class="notice">
      该 SKU 暂无来源图片，请关闭后使用“本地添加图片”。
    </p>
    <div class="picker-grid">
      <label
        v-for="(url, i) in available"
        :key="url"
        :class="{ chosen: selected.includes(url) || existing.includes(url) }"
      >
        <ProductImage :src="url" image-class="picker-image" />
        <span
          ><input
            v-if="!existing.includes(url)"
            v-model="selected"
            type="checkbox"
            :value="url"
            :disabled="!selected.includes(url) && selected.length >= remaining"
            :aria-label="'选择来源图片 ' + (i + 1)"
          />
          {{ existing.includes(url) ? '已添加' : '图片 ' + (i + 1) }}</span
        >
      </label>
    </div>
    <footer>
      <button class="btn" @click="emit('close')">取消</button
      ><button class="btn primary" :disabled="!selected.length" @click="apply">
        添加选中图片（{{ selected.length }}）
      </button>
    </footer>
  </dialog>
</template>
<style scoped>
.listing-picker {
  width: min(800px, 92vw);
  max-height: 85vh;
  overflow: auto;
  border: 1px solid #dbe4ed;
  border-radius: 20px;
  padding: 24px;
  color: #334155;
  background: #f8fafc;
}
.listing-picker::backdrop {
  background: #24364a55;
}
header,
footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
header h3 {
  margin: 0;
}
header p {
  color: #64748b;
  font-size: 14px;
}
header .icon-btn {
  background: #e3ebf3;
  border-radius: 12px;
  flex-shrink: 0;
}
.picker-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 14px;
  margin: 16px 0;
}
.picker-grid label {
  display: grid;
  gap: 10px;
  padding: 10px;
  background: white;
  border: 2px solid #e2e8f0;
  border-radius: 12px;
  cursor: pointer;
}
.picker-grid label.chosen {
  border-color: #7894b1;
  background: #edf3f8;
}
.picker-grid :deep(.picker-image) {
  width: 100%;
  height: 140px;
  object-fit: contain;
}
.picker-grid span {
  display: flex;
  align-items: center;
  gap: 8px;
}
footer {
  justify-content: flex-end;
  position: sticky;
  bottom: -24px;
  background: #f8fafc;
  padding: 16px 0;
}
</style>
