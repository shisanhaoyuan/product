<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
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
const emit = defineEmits<{ close: []; apply: [urls: string[]] }>();
const dialog = ref<HTMLDialogElement>();
const candidates = ref<string[]>([...new Set([...props.existing, ...props.sourceImages])]);
const selected = ref<string[]>([...new Set(props.existing)]);
const loading = ref(true),
  error = ref('');
const available = computed(() => [...new Set(candidates.value)].filter(Boolean));
const allSelected = computed(
  () => available.value.length > 0 && available.value.every((url) => selected.value.includes(url)),
);
function selectAll() {
  selected.value = [...new Set([...selected.value, ...available.value])];
}
function apply() {
  emit('apply', [...selected.value]);
  emit('close');
}
let live = true;
onBeforeUnmount(() => {
  live = false;
});
onMounted(async () => {
  dialog.value?.showModal();
  try {
    const product = await catalogApi.product(props.listingId);
    if (!live) return;
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
  <dialog ref="dialog" class="listing-picker" aria-label="编辑商品图片" @close="emit('close')">
    <header>
      <div>
        <h3>编辑商品图片</h3>
        <p>{{ sku }} · 已选 {{ selected.length }} 张 · 点击可选择或取消，不限数量</p>
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
    <p class="picker-hint">
      第一张选中的图片为主图；取消主图后，下一张自动成为主图。修改后点击“应用选择”保存。
    </p>
    <div class="picker-selection-actions">
      <button class="btn" :disabled="!available.length || allSelected" @click="selectAll">
        <AppIcon name="Check" />全选图片
      </button>
      <button class="btn" :disabled="!selected.length" @click="selected = []">取消全选</button>
      <span>已选 {{ selected.length }} / 共 {{ available.length }} 张</span>
    </div>
    <div class="picker-grid">
      <label v-for="(url, i) in available" :key="url" :class="{ chosen: selected.includes(url) }">
        <ProductImage :src="url" image-class="picker-image" />
        <span
          ><input
            v-model="selected"
            type="checkbox"
            :value="url"
            :aria-label="'选择来源图片 ' + (i + 1)"
          />
          {{
            selected[0] === url
              ? '主图'
              : selected.includes(url)
                ? '已选 · ' + (selected.indexOf(url) + 1)
                : '图片 ' + (i + 1)
          }}</span
        >
      </label>
    </div>
    <footer>
      <button class="btn" @click="emit('close')">取消</button
      ><button class="btn primary" @click="apply">应用选择（{{ selected.length }}）</button>
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
.picker-hint {
  padding: 12px 14px;
  border-radius: 10px;
  background: #e7eef5;
  color: #46627b;
  line-height: 1.6;
}
.picker-selection-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  padding: 4px 0 8px;
}
.picker-selection-actions .btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: #e4edf5;
  color: #34546e;
  border-color: #bdcfde;
  padding: 10px 16px;
  font-size: 14px;
}
.picker-selection-actions span {
  color: #64748b;
  font-size: 13px;
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
