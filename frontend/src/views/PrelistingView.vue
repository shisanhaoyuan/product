<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { prelistingApi } from '../api/prelisting';
import type { BatchPage, PrelistingBatch, ListingStore } from '../api/types';
import { useAuth } from '../composables/useAuth';
import { errorMessage } from '../utils/display';
import PrelistingPreview from '../components/PrelistingPreview.vue';
import WalmartListingEditor from '../components/WalmartListingEditor.vue';
import FilterSelect from '../components/FilterSelect.vue';
const route = useRoute();
const { auth } = useAuth();
const canEdit = computed(
  () => !auth.enabled || ['admin', 'editor'].includes(auth.user?.role || ''),
);
const data = ref<BatchPage>(),
  batch = ref<PrelistingBatch>(),
  loading = ref(false),
  busy = ref(false),
  error = ref(''),
  message = ref(''),
  page = ref(1);
const labels = { draft: '上架准备中', confirmed: '已确认', cancelled: '已取消' };
const status = ref(''),
  storeId = ref(''),
  query = ref(''),
  trash = ref(false);
const stores = ref<ListingStore[]>([]),
  selected = ref<string[]>([]);
const bulkDialog = ref<HTMLDialogElement>();
const bulkMode = ref<'delete' | 'restore'>('delete');
const selectable = computed(
  () => data.value?.items.filter((row) => row.status === 'cancelled').map((row) => row.id) || [],
);
const allSelected = computed(
  () => !!selectable.value.length && selectable.value.every((id) => selected.value.includes(id)),
);
const partlySelected = computed(() => !!selected.value.length && !allSelected.value);
function toggle(id: string) {
  selected.value = selected.value.includes(id)
    ? selected.value.filter((value) => value !== id)
    : [...selected.value, id];
}
function togglePage() {
  selected.value = allSelected.value ? [] : [...selectable.value];
}
function confirmBulk() {
  if (!selected.value.length) return;
  bulkMode.value = trash.value ? 'restore' : 'delete';
  bulkDialog.value?.showModal();
}
async function bulkAction() {
  if (busy.value || !selected.value.length) return;
  busy.value = true;
  error.value = '';
  try {
    const result = await prelistingApi.bulkAction([...selected.value], bulkMode.value);
    bulkDialog.value?.close();
    await load();
    message.value =
      bulkMode.value === 'delete'
        ? `已删除 ${result.changed} 条记录，可在回收站恢复。`
        : `已恢复 ${result.changed} 条记录，批次仍为已取消，不会重新上架。`;
  } catch (e) {
    error.value = errorMessage(e);
    bulkDialog.value?.close();
  } finally {
    busy.value = false;
  }
}
const legacySkuBatch = computed(
  () =>
    !!batch.value &&
    !['my_store_random6_v1', 'my_store_random6_v2'].includes(batch.value.preview.sku_rule || ''),
);
const needsMixedSku = computed(
  () => batch.value?.status === 'draft' && batch.value.preview.sku_rule === 'my_store_random6_v1',
);
let controller: AbortController | undefined;
async function load() {
  controller?.abort();
  const current = new AbortController();
  controller = current;
  loading.value = true;
  error.value = '';
  batch.value = undefined;
  message.value = '';
  selected.value = [];
  try {
    if (typeof route.params.id === 'string') {
      const result = await prelistingApi.batch(route.params.id, current.signal);
      if (!current.signal.aborted) batch.value = result;
    } else {
      const result = await prelistingApi.batches(page.value, current.signal, {
        status: status.value,
        store_id: storeId.value || '0',
        q: query.value.trim(),
        trash: String(trash.value),
      });
      const lastPage = Math.max(1, Math.ceil(result.total / result.page_size));
      if (!current.signal.aborted && page.value > lastPage) {
        page.value = lastPage;
        await load();
        return;
      }
      if (!current.signal.aborted) data.value = result;
    }
  } catch (e) {
    if (!current.signal.aborted) error.value = errorMessage(e);
  } finally {
    if (!current.signal.aborted) loading.value = false;
  }
}
async function act(action: 'confirm' | 'cancel' | 'upgrade_skus') {
  if (!batch.value || busy.value) return;
  busy.value = true;
  error.value = '';
  message.value = '';
  try {
    batch.value = await prelistingApi.action(batch.value.id, action);
    message.value =
      action === 'upgrade_skus'
        ? '目标 SKU 已升级为字母和数字混合尾码。'
        : action === 'confirm'
          ? '批次已确认。请在商品刊登资料中校验并提交沃尔玛。'
          : '批次已取消，可以重新选择商品创建。';
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
function next(offset: number) {
  page.value += offset;
  void load();
}
watch(
  () => route.fullPath,
  () => {
    page.value = 1;
    void load();
  },
  { immediate: true },
);
watch([status, storeId, trash], () => {
  page.value = 1;
  void load();
});
let searchTimer: ReturnType<typeof setTimeout> | undefined;
watch(query, () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    page.value = 1;
    void load();
  }, 250);
});
onMounted(async () => {
  try {
    stores.value = await prelistingApi.stores();
  } catch (e) {
    error.value = errorMessage(e);
  }
});
onBeforeUnmount(() => {
  controller?.abort();
  clearTimeout(searchTimer);
});
</script>
<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">LISTING / 上架准备</p>
      <h1>{{ route.params.id ? '批次详情' : '预上架批次' }}</h1>
    </div>
    <div class="actions">
      <RouterLink class="btn" to="/giga-products">选择云仓商品</RouterLink>
      <RouterLink class="btn primary" to="/products">选择亚马逊商品</RouterLink>
    </div>
  </div>
  <p v-if="!route.params.id" class="notice notice-space">
    打开批次后填写刊登资料，按“校验上架资料 → 提交沃尔玛 →
    查看处理结果”完成上架。正式提交仅限管理员。
  </p>
  <div v-if="!route.params.id" class="batch-filters">
    <FilterSelect
      v-model="status"
      label="批次状态"
      :options="[
        { value: '', label: '全部状态' },
        { value: 'draft', label: '上架准备中' },
        { value: 'confirmed', label: '已确认' },
        { value: 'cancelled', label: '已取消' },
      ]"
    />
    <FilterSelect
      v-model="storeId"
      label="目标店铺"
      :options="[
        { value: '', label: '全部店铺' },
        ...stores.map((store) => ({ value: String(store.id), label: store.name })),
      ]"
    />
    <input
      v-model="query"
      type="search"
      aria-label="搜索批次、店铺或品牌"
      placeholder="搜索批次、店铺或品牌"
    />
    <button class="btn" :disabled="busy" @click="trash = !trash">
      {{ trash ? '返回批次列表' : '回收站' }}
    </button>
  </div>
  <p v-if="message" role="status" class="notice notice-space">{{ message }}</p>
  <div v-if="error" role="alert" class="error-box">
    {{ error }}<button class="btn small" @click="load">刷新</button>
  </div>
  <div v-if="loading" class="loading">正在加载批次…</div>
  <section v-else-if="batch" class="section">
    <div class="prelisting-detail-meta">
      <div>
        <p>
          {{ batch.deleted_at ? '已移入回收站 · ' : ''
          }}{{ batch.status === 'cancelled' ? '已取消 · ' : '' }}批次 {{ batch.id }} ·
          {{ new Date(batch.created_at).toLocaleString('zh-CN') }}
        </p>
      </div>
      <RouterLink class="btn small" to="/prelisting">返回批次列表</RouterLink>
    </div>
    <div class="section-body">
      <p v-if="legacySkuBatch" class="notice notice-space" role="status">
        此批次未按新规则分配目标店铺 SKU。请先在店铺管理中设置目标店铺唯一的两位编码，
        再取消此批次并重新创建。新批次将生成 MY + 两位店铺编码 + 六位随机字母和数字。
        <RouterLink to="/listing-stores">前往店铺管理</RouterLink>
      </p>
      <p v-if="needsMixedSku" class="notice notice-space" role="status">
        此批次使用旧版数字尾码，可升级为六位大写字母和数字混合尾码。
        <button v-if="canEdit" class="btn small" :disabled="busy" @click="act('upgrade_skus')">
          升级 SKU
        </button>
      </p>
      <WalmartListingEditor
        v-if="batch.status !== 'cancelled' && !legacySkuBatch && !needsMixedSku"
        :key="batch.id"
        :bid="batch.id"
      />
      <details class="listing-original-preview">
        <summary>查看创建批次时的来源预览</summary>
        <PrelistingPreview :preview="batch.preview" />
      </details>
      <div v-if="canEdit && batch.status !== 'cancelled'" class="plan-footer">
        <button class="btn" :disabled="busy" @click="act('cancel')">取消批次</button>
      </div>
    </div>
  </section>
  <section v-else-if="data" class="section">
    <div v-if="canEdit" class="batch-actions">
      <label class="btn small"
        ><input
          type="checkbox"
          :checked="allSelected"
          :indeterminate="partlySelected"
          :disabled="busy || !selectable.length"
          @change="togglePage"
        />全选本页已取消记录</label
      >
      <span class="muted"
        >已选 {{ selected.length }} 条{{
          trash ? ' · 恢复后仍为已取消' : ' · 仅已取消记录可删除'
        }}</span
      >
      <button class="btn small" :disabled="busy || !selected.length" @click="confirmBulk">
        {{ trash ? '批量恢复记录' : '批量删除记录' }}
      </button>
    </div>
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th v-if="canEdit">选择</th>
            <th>创建时间</th>
            <th>目标店铺 / 品牌</th>
            <th>倍率</th>
            <th>商品 / SKU 行</th>
            <th>预检查通过</th>
            <th>本地准备状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in data.items" :key="row.id">
            <td v-if="canEdit">
              <input
                type="checkbox"
                :aria-label="`选择批次 ${row.id}`"
                :checked="selected.includes(row.id)"
                :disabled="busy || row.status !== 'cancelled'"
                @change="toggle(row.id)"
              />
            </td>
            <td>{{ new Date(row.created_at).toLocaleString('zh-CN') }}</td>
            <td>{{ row.store }}<br />{{ row.brand }}</td>
            <td>× {{ row.multiplier }}</td>
            <td>{{ row.total }} / {{ row.variant_count }}</td>
            <td>{{ row.ready_count }} / {{ row.total }}</td>
            <td>{{ labels[row.status] }}</td>
            <td>
              <RouterLink class="detail-link" :to="'/prelisting/' + row.id">检查批次 ↗</RouterLink>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="!data.items.length" class="empty">
      {{ trash ? '回收站没有匹配记录' : '没有符合当前条件的批次' }}
      <p>
        {{
          trash
            ? '已删除的记录会显示在这里，可批量恢复。'
            : '可调整筛选条件，或前往商品库创建预上架批次。'
        }}
      </p>
    </div>
    <div class="pagination">
      <span>共 {{ data.total }} 批 · 第 {{ page }} 页</span>
      <div class="actions">
        <button class="btn small" :disabled="page <= 1" @click="next(-1)">上一页</button
        ><button class="btn small" :disabled="page * data.page_size >= data.total" @click="next(1)">
          下一页
        </button>
      </div>
    </div>
  </section>
  <dialog ref="bulkDialog" class="batch-confirm" @cancel.prevent="!busy && bulkDialog?.close()">
    <h3>{{ bulkMode === 'delete' ? '删除已取消记录' : '恢复记录' }}</h3>
    <p>
      {{
        bulkMode === 'delete'
          ? `将所选 ${selected.length} 条记录移到回收站，从批次列表隐藏。商品资料和已分配编码会保留。`
          : `恢复所选 ${selected.length} 条记录的列表显示；不会恢复上架任务。`
      }}
    </p>
    <div class="actions">
      <button class="btn" :disabled="busy" @click="bulkDialog?.close()">返回</button
      ><button class="btn primary" :disabled="busy" @click="bulkAction">
        {{ busy ? '正在处理…' : '确认' }}
      </button>
    </div>
  </dialog>
</template>
<style scoped>
.batch-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin: 20px 0;
}
.batch-filters > * {
  min-width: 160px;
}
.batch-filters input {
  flex: 1;
}
.batch-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 16px;
  border-bottom: 1px solid var(--line);
}
.batch-actions label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.batch-confirm {
  max-width: 480px;
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 28px;
  color: var(--text);
}
.batch-confirm::backdrop {
  background: #25364855;
}
</style>
