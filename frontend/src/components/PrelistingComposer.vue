<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { prelistingApi } from '../api/prelisting';
import type {
  ListingStore,
  PlanInput,
  PrelistingPreview as Preview,
  PrelistingSelection,
  TemplateOptions,
} from '../api/types';
import { errorMessage } from '../utils/display';
import { collectMissingPrices } from '../utils/sourceCollection';
import PrelistingPreview from './PrelistingPreview.vue';
import AppIcon from './AppIcon.vue';
import FilterSelect from './FilterSelect.vue';
const props = defineProps<{ selection: PrelistingSelection; count: number; source?: string }>();
const emit = defineEmits<{ close: []; created: [id: string] }>();
const dialog = ref<HTMLDialogElement>();
const stores = ref<ListingStore[]>([]),
  storeId = ref(''),
  brand = ref(''),
  multiplier = ref('');
const collectionProgress = ref('');
const busy = ref(false),
  error = ref(''),
  preview = ref<Preview>();
const templates = ref<TemplateOptions['items']>([]);
const selectedTemplates = ref<Record<string, string>>({});
const templatesLoading = ref(false);
const storeOptions = computed(() =>
  stores.value
    .filter(
      (store) =>
        store.active &&
        (store.marketplace || 'US') === (props.source === 'GigaB2B_CA' ? 'CA' : 'US'),
    )
    .map((store) => ({
      value: String(store.id),
      label:
        store.name + (store.marketplace_name !== store.name ? ' · ' + store.marketplace_name : ''),
    })),
);
let templateController: AbortController | undefined;
let submitted: PlanInput | undefined;
let requestKey = '';
let controller: AbortController | undefined;
const valid = computed(
  () =>
    !!storeId.value &&
    !templatesLoading.value &&
    !!brand.value.trim() &&
    Number(multiplier.value) > 0 &&
    Number(multiplier.value) <= 100,
);
watch(
  storeId,
  (value) => {
    brand.value = stores.value.find((store) => String(store.id) === value)?.default_brand || '';
    void loadTemplates();
  },
  { flush: 'sync' },
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
watch(
  selectedTemplates,
  () => {
    if (!multiplier.value) {
      const chosen = templates.value.flatMap((item) =>
        item.options.filter(
          (option) => option.batch_id === selectedTemplates.value[item.listing_id],
        ),
      );
      if (chosen[0]) multiplier.value = chosen[0].multiplier;
    }
    preview.value = undefined;
    submitted = undefined;
    requestKey = '';
  },
  { deep: true },
);
async function loadTemplates() {
  templateController?.abort();
  templates.value = [];
  selectedTemplates.value = {};
  if (!storeId.value) return;
  const current = new AbortController();
  templateController = current;
  templatesLoading.value = true;
  error.value = '';
  try {
    const result = await prelistingApi.templates(
      {
        ...props.selection,
        store_id: Number(storeId.value),
        brand: brand.value || '待设置',
        multiplier: '1',
      },
      current.signal,
    );
    if (!current.signal.aborted) templates.value = result.items;
  } catch (e) {
    if (!current.signal.aborted) error.value = errorMessage(e);
  } finally {
    if (!current.signal.aborted) templatesLoading.value = false;
  }
}
onMounted(async () => {
  dialog.value?.showModal();
  try {
    stores.value = await prelistingApi.stores();
  } catch (e) {
    error.value = errorMessage(e);
  }
});
onBeforeUnmount(() => {
  controller?.abort();
  templateController?.abort();
});
async function calculate() {
  if (busy.value) return;
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
    template_batches: Object.fromEntries(
      Object.entries(selectedTemplates.value).filter(([, value]) => !!value),
    ),
  };
  try {
    const signal = controller.signal;
    let result = await prelistingApi.preview(input, signal);
    const collected = await collectMissingPrices(result, signal, (message) => {
      collectionProgress.value = message;
    });
    if (collected.attempted) result = await prelistingApi.preview(input, signal);
    signal.throwIfAborted();
    preview.value = result;
    if (collected.errors.length) error.value = collected.errors.join('；');
    submitted = input;
    requestKey = crypto.randomUUID();
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
    collectionProgress.value = '';
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
      <p v-if="collectionProgress" class="notice" role="status">{{ collectionProgress }}</p>
      <button v-if="collectionProgress" class="btn small" @click="controller?.abort()">
        取消采集
      </button>
      <form id="prelisting-settings" class="plan-form composer-form" @submit.prevent="calculate">
        <div class="composer-store-field">
          <span>上架店铺</span>
          <FilterSelect
            v-model="storeId"
            class="composer-store-select"
            label="选择目标店铺"
            :options="storeOptions"
            :disabled="busy"
            searchable
            search-placeholder="搜索店铺名称，例如 MYZ、21"
          />
        </div>
        <label
          >品牌<input
            v-model="brand"
            required
            maxlength="100"
            placeholder="选择店铺后自动带入，也可手动填写"
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
      <section v-if="storeId" class="template-reuse" aria-label="复用已填写模板">
        <h3>复用已填写模板</h3>
        <p class="muted">
          保留类目、属性、图片和文案，自动替换本店品牌。倍率为空时沿用所选模板；售价按本次倍率计算，平台
          SKU、编码与发货仓按目标店铺处理，提交前需重新校验。
        </p>
        <p v-if="templatesLoading" role="status">正在查找该商品的历史模板…</p>
        <p v-else-if="!templates.some((item) => item.options.length)" class="muted">
          所选商品暂无覆盖对应子 SKU 的已填写模板，可按原流程新建。
        </p>
        <label
          v-for="item in templates.filter((item) => item.options.length)"
          :key="item.listing_id"
          class="template-reuse-item"
        >
          <span :title="item.title">{{ item.title }}</span>
          <select
            v-model="selectedTemplates[item.listing_id]"
            :disabled="busy"
            :aria-label="item.title + '的刊登模板'"
          >
            <option :value="undefined">不复用，重新填写</option>
            <option v-for="option in item.options" :key="option.batch_id" :value="option.batch_id">
              {{ option.store_name }} · {{ option.brand }} · {{ option.sku_count }} 个 SKU ·
              {{ option.updated_at.slice(0, 10) }} · {{ option.batch_id.slice(0, 8) }}
            </option>
          </select>
        </label>
      </section>
      <div class="composer-actions">
        <p class="composer-hint">
          {{
            source === 'Saleyee'
              ? '赛盈采购价（不另加运费）'
              : ['GigaB2B', 'GigaB2B_CA'].includes(source || '')
                ? '来源普通单价＋物流费（含打包）'
                : '来源采集原价'
          }}
          × 本次倍率<br />
          <span>{{
            ['GigaB2B', 'GigaB2B_CA'].includes(source || '') || source === 'Saleyee'
              ? '供应商按供应商 SKU 独立计价 · 保留两位小数'
              : '按当前 SKU／ASIN 计价 · 保留两位小数 · 缺少原价自动采集'
          }}</span>
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
<style scoped>
.composer-form {
  align-items: start;
}
.composer-store-field {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  color: #506078;
  font-size: 12px;
  font-weight: 550;
}
.composer-store-select {
  width: 100%;
}
/* Keep the menu inside the modal's layout so its scroll boundary cannot clip it. */
.composer-store-select :deep(.filter-select-options) {
  position: relative;
  top: auto;
  margin-top: 6px;
  box-sizing: border-box;
  max-height: min(280px, 40vh);
  overscroll-behavior: contain;
}
.composer-store-select :deep(summary) {
  box-sizing: border-box;
  height: 44px;
  font-size: 13px;
  font-weight: 400;
  color: var(--ink);
}
.composer-store-select :deep(.filter-select-options button) {
  font-size: 14px;
  min-height: 40px;
}
@media (max-width: 600px) {
  .composer-store-field {
    grid-column: 1 / -1;
  }
}
</style>
