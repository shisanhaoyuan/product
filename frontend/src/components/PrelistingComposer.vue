<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { prelistingApi } from '../api/prelisting';
import type {
  ListingStore,
  PlanInput,
  PrelistingPreview as Preview,
  PrelistingSelection,
} from '../api/types';
import { errorMessage } from '../utils/display';
import PrelistingPreview from './PrelistingPreview.vue';
import AppIcon from './AppIcon.vue';
const props = defineProps<{ selection: PrelistingSelection; count: number }>();
const emit = defineEmits<{ close: []; created: [id: string] }>();
const dialog = ref<HTMLDialogElement>();
const stores = ref<ListingStore[]>([]),
  storeId = ref(''),
  brand = ref(''),
  multiplier = ref('');
const busy = ref(false),
  error = ref(''),
  preview = ref<Preview>();
let submitted: PlanInput | undefined;
let requestKey = '';
let controller: AbortController | undefined;
const valid = computed(
  () =>
    !!storeId.value &&
    !!brand.value.trim() &&
    Number(multiplier.value) > 0 &&
    Number(multiplier.value) <= 100,
);
watch(
  [storeId, brand, multiplier],
  () => {
    preview.value = undefined;
    submitted = undefined;
    requestKey = '';
  },
  { flush: 'sync' },
);
onMounted(async () => {
  dialog.value?.showModal();
  try {
    stores.value = await prelistingApi.stores();
  } catch (e) {
    error.value = errorMessage(e);
  }
});
onBeforeUnmount(() => controller?.abort());
async function calculate() {
  busy.value = true;
  error.value = '';
  preview.value = undefined;
  controller?.abort();
  controller = new AbortController();
  const input: PlanInput = {
    ...props.selection,
    store_id: Number(storeId.value),
    brand: brand.value.trim(),
    multiplier: multiplier.value,
  };
  try {
    preview.value = await prelistingApi.preview(input, controller.signal);
    submitted = input;
    requestKey = crypto.randomUUID();
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
async function create() {
  if (!preview.value || !submitted || busy.value) return;
  busy.value = true;
  error.value = '';
  try {
    const batch = await prelistingApi.create(submitted, preview.value.preview_hash, requestKey);
    emit('created', batch.id);
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <dialog
    ref="dialog"
    class="prelisting-dialog"
    :class="{ 'has-preview': !!preview }"
    aria-labelledby="plan-dialog-title"
    @cancel.prevent="!busy && emit('close')"
  >
    <div class="composer-header">
      <div>
        <h2 id="plan-dialog-title">批量预上架</h2>
        <p>
          {{
            selection.variant_skus
              ? '所选子变体'
              : selection.filters
                ? '当前筛选的全部结果（跨页）'
                : '手动勾选商品'
          }}
          · {{ count }} {{ selection.variant_skus ? '个 SKU' : '件' }}
        </p>
      </div>
      <button
        type="button"
        class="composer-close"
        aria-label="关闭预上架弹窗"
        title="关闭"
        :disabled="busy"
        @click="emit('close')"
      >
        <AppIcon name="X" />
      </button>
    </div>
    <div class="composer-body">
      <form id="prelisting-settings" class="plan-form composer-form" @submit.prevent="calculate">
        <label
          >上架店铺<select v-model="storeId" required :disabled="busy">
            <option value="" disabled>选择目标店铺</option>
            <option v-for="s in stores.filter((s) => s.active)" :key="s.id" :value="String(s.id)">
              {{ s.name }}{{ s.marketplace_name !== s.name ? ' · ' + s.marketplace_name : '' }}
            </option>
          </select></label
        >
        <label
          >品牌<input
            v-model="brand"
            required
            maxlength="100"
            placeholder="输入本批品牌"
            :disabled="busy"
        /></label>
        <label
          >价格倍率<input
            v-model="multiplier"
            type="number"
            required
            min="0.0001"
            max="100"
            step="0.0001"
            placeholder="例如 1.15"
            :disabled="busy"
        /></label>
      </form>
      <div class="composer-actions">
        <p class="composer-hint">
          基准价格 × 本次倍率<br /><span>多变体 .99 尾价 · 单商品保留两位小数</span>
        </p>
        <button
          form="prelisting-settings"
          class="btn primary composer-preview"
          :disabled="busy || !valid"
        >
          {{ busy ? '正在处理…' : preview ? '重新生成预览' : '生成预览'
          }}<AppIcon name="ArrowRight" />
        </button>
      </div>
      <div v-if="error" role="alert" class="error-box">{{ error }}</div>
      <template v-if="preview"
        ><PrelistingPreview :preview="preview" />
        <div class="plan-footer">
          <span class="muted">保存为待确认批次，尚未提交到平台。</span
          ><button class="btn primary" :disabled="busy || !preview.can_create" @click="create">
            {{ busy ? '正在保存…' : '创建待上架批次' }}
          </button>
        </div></template
      >
    </div>
  </dialog>
</template>
