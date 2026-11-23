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
import AppIcon from '../components/AppIcon.vue';
import DateTimeRangeFilter from '../components/DateTimeRangeFilter.vue';
import { dateTimeRangeQuery } from '../utils/dateTimeRange';
import { publishingApi } from '../api/publishing';
import {
  activeGeneration,
  forEachBatch,
  generationResult,
  runBatchPreparation,
} from '../utils/batchPreparation';
import type { BatchPreparationResult } from '../utils/batchPreparation';
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
  createdStart = ref(''),
  createdEnd = ref(''),
  trash = ref(false);
const stores = ref<ListingStore[]>([]),
  selected = ref<string[]>([]);
const bulkDialog = ref<HTMLDialogElement>();
const bulkMode = ref<'delete' | 'restore'>('delete');
const bulkIds = ref<string[]>([]);
const selectable = computed(() => data.value?.items.map((row) => row.id) || []);
const allSelected = computed(
  () => !!selectable.value.length && selectable.value.every((id) => selected.value.includes(id)),
);
const partlySelected = computed(() => !!selected.value.length && !allSelected.value);
const bulkRunning = ref(false),
  preparationMode = ref<'generate' | 'validate'>('generate'),
  preparationResults = ref<BatchPreparationResult[]>([]);
const preparationNames = ref<Record<string, string>>({});
const preparationLabels: Record<BatchPreparationResult['state'], string> = {
  waiting: '等待开始',
  starting: '处理中',
  awaiting_sources: '等待采集',
  queued: '已排队',
  running: 'AI 生成中',
  complete: '已完成',
  review: '需要修正',
  failed: '未完成',
  skipped: '未执行',
};
let preparationController: AbortController | undefined;
let preparationTimer: ReturnType<typeof setTimeout> | undefined;
let pollingPreparation = false;
let live = true;
function updatePreparation(result: BatchPreparationResult) {
  if (!live) return;
  preparationResults.value = preparationResults.value.map((row) =>
    row.id === result.id ? result : row,
  );
  if (activeGeneration(result.state) && !preparationTimer && !pollingPreparation)
    preparationTimer = setTimeout(() => void pollPreparation(), 2500);
}
async function pollPreparation() {
  if (pollingPreparation || !live) return;
  pollingPreparation = true;
  clearTimeout(preparationTimer);
  preparationTimer = undefined;
  try {
    await forEachBatch(
      preparationResults.value.filter((row) => activeGeneration(row.state)),
      async (row) => {
        try {
          const value = await publishingApi.generation(row.id);
          if (
            !live ||
            !preparationResults.value.some(
              (current) => current.id === row.id && current.jobId === row.jobId,
            )
          )
            return;
          if (value.job && value.job.id === row.jobId)
            updatePreparation(generationResult(row.id, value.job));
          else
            updatePreparation({
              ...row,
              state: 'failed',
              message: '生成任务已变化，请打开批次查看最新结果',
            });
        } catch (error) {
          if (
            live &&
            preparationResults.value.some(
              (current) => current.id === row.id && current.jobId === row.jobId,
            )
          )
            updatePreparation({
              ...row,
              state: 'failed',
              message: `进度查询未完成：${errorMessage(error)}；可打开批次查看`,
            });
        }
      },
    );
  } finally {
    pollingPreparation = false;
  }
  if (live && preparationResults.value.some((row) => activeGeneration(row.state)))
    preparationTimer = setTimeout(() => void pollPreparation(), 2500);
}
async function prepareSelected(mode: 'generate' | 'validate') {
  if (busy.value || bulkRunning.value || !selected.value.length) return;
  clearTimeout(preparationTimer);
  preparationTimer = undefined;
  preparationController?.abort();
  const current = new AbortController();
  preparationController = current;
  const ids = [...selected.value];
  preparationMode.value = mode;
  preparationNames.value = Object.fromEntries(
    (data.value?.items || []).map((row) => [row.id, `${row.store} / ${row.brand}`]),
  );
  preparationResults.value = ids.map((id) => ({ id, state: 'waiting', message: '等待开始' }));
  bulkRunning.value = true;
  error.value = '';
  message.value = '';
  try {
    await runBatchPreparation(ids, mode, current.signal, updatePreparation);
  } finally {
    if (live) {
      bulkRunning.value = false;
      if (mode === 'generate') void pollPreparation();
    }
  }
}
function toggle(id: string) {
  selected.value = selected.value.includes(id)
    ? selected.value.filter((value) => value !== id)
    : [...selected.value, id];
}
function togglePage() {
  selected.value = allSelected.value ? [] : [...selectable.value];
}
function confirmBulk(ids = selected.value) {
  if (busy.value || bulkRunning.value || !ids.length) return;
  bulkIds.value = [...ids];
  bulkMode.value = trash.value ? 'restore' : 'delete';
  bulkDialog.value?.showModal();
}
async function bulkAction() {
  if (busy.value || !bulkIds.value.length) return;
  busy.value = true;
  error.value = '';
  try {
    const result = await prelistingApi.bulkAction([...bulkIds.value], bulkMode.value);
    bulkDialog.value?.close();
    if (bulkMode.value === 'delete')
      preparationResults.value = preparationResults.value.filter(
        (row) => !bulkIds.value.includes(row.id),
      );
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
        ...dateTimeRangeQuery(createdStart.value, createdEnd.value),
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
async function act(action: 'confirm' | 'upgrade_skus') {
  if (!batch.value || busy.value) return;
  busy.value = true;
  error.value = '';
  message.value = '';
  try {
    batch.value = await prelistingApi.action(batch.value.id, action);
    message.value =
      action === 'upgrade_skus'
        ? '目标 SKU 已升级为字母和数字混合尾码。'
        : '批次已确认。请在商品刊登资料中校验并提交沃尔玛。';
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
    if (route.params.id) preparationController?.abort();
    page.value = 1;
    void load();
  },
  { immediate: true },
);
watch([status, storeId, trash, createdStart, createdEnd], () => {
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
  live = false;
  preparationController?.abort();
  clearTimeout(preparationTimer);
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
      <RouterLink class="btn" to="/walmart-review">沃尔玛审核进度</RouterLink>
      <RouterLink class="btn" to="/giga-products">选择云仓商品</RouterLink>
      <RouterLink class="btn primary" to="/products">选择亚马逊商品</RouterLink>
    </div>
  </div>
  <p v-if="!route.params.id" class="notice notice-space">
    打开批次后填写刊登资料，按“校验上架资料 → 提交沃尔玛 →
    查看处理结果”完成上架。提交后移至审核进度，失败时返回并标记“审核失败”。正式提交仅限管理员。
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
        { value: 'review_failed', label: '审核失败' },
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
    <div class="batch-search">
      <AppIcon name="Search" />
      <input
        v-model="query"
        type="search"
        aria-label="搜索批次、店铺或品牌"
        placeholder="搜索批次、店铺或品牌"
      />
      <button
        v-if="query"
        type="button"
        class="batch-search-clear"
        aria-label="清空批次搜索"
        @click="query = ''"
      >
        <AppIcon name="X" />
      </button>
    </div>
    <DateTimeRangeFilter
      class="batch-time-filter"
      :start="createdStart"
      :end="createdEnd"
      @apply="
        (start, end) => {
          createdStart = start;
          createdEnd = end;
        }
      "
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
        再删除此批次并重新创建。新批次将生成 MY + 两位店铺编码 + 六位随机字母和数字。
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
      <div v-if="canEdit && !batch.deleted_at" class="plan-footer">
        <button class="btn" :disabled="busy || bulkRunning" @click="confirmBulk([batch.id])">
          删除记录
        </button>
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
          :disabled="busy || bulkRunning || !selectable.length"
          @change="togglePage"
        />全选本页记录</label
      >
      <span class="muted"
        >已选 {{ selected.length }} 条{{
          trash ? ' · 恢复后仍为已取消' : ' · 填充、审核和删除共用所选记录'
        }}</span
      >
      <button
        v-if="!trash"
        class="btn batch-generate"
        :disabled="busy || bulkRunning || !selected.length"
        @click="prepareSelected('generate')"
      >
        AI 批量填充
      </button>
      <button
        v-if="!trash"
        class="btn batch-validate"
        :disabled="busy || bulkRunning || !selected.length"
        @click="prepareSelected('validate')"
      >
        批量审核
      </button>
      <button
        class="btn small"
        :disabled="busy || bulkRunning || !selected.length"
        @click="confirmBulk()"
      >
        {{ trash ? '批量恢复记录' : '批量删除记录' }}
      </button>
    </div>
    <section
      v-if="preparationResults.length && !trash"
      class="batch-preparation"
      aria-label="批量处理结果"
      aria-live="polite"
    >
      <div class="batch-preparation-heading">
        <strong>{{
          preparationMode === 'generate' ? 'AI 批量填充进度' : '批量资料审核结果'
        }}</strong>
        <span
          >{{ preparationResults.length }} 个批次 ·
          {{
            preparationResults.filter((row) =>
              ['complete', 'review', 'failed', 'skipped'].includes(row.state),
            ).length
          }}
          个已结束</span
        >
        <button v-if="bulkRunning" class="btn small" @click="preparationController?.abort()">
          停止后续操作
        </button>
      </div>
      <p class="muted">
        {{
          preparationMode === 'generate'
            ? '已入队任务在后台继续执行；亚马逊商品沿用本机浏览器采集。'
            : '按沃尔玛类目规则校验当前已保存资料，未通过的项目会逐批列出。'
        }}
      </p>
      <article v-for="result in preparationResults" :key="result.id" class="batch-preparation-row">
        <div>
          <RouterLink :to="'/prelisting/' + result.id"
            >{{ preparationNames[result.id] || result.id }} ↗</RouterLink
          >
          <small>批次 {{ result.id }}</small>
        </div>
        <span class="batch-preparation-state" :class="result.state">{{
          preparationLabels[result.state]
        }}</span>
        <p>{{ result.message }}</p>
        <details v-if="result.errors?.length">
          <summary>查看 {{ result.errors.length }} 项具体原因</summary>
          <ul>
            <li v-for="issue in result.errors" :key="issue">{{ issue }}</li>
          </ul>
        </details>
      </article>
    </section>
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
                :disabled="busy || bulkRunning"
                @change="toggle(row.id)"
              />
            </td>
            <td>{{ new Date(row.created_at).toLocaleString('zh-CN') }}</td>
            <td>{{ row.store }}<br />{{ row.brand }}</td>
            <td>× {{ row.multiplier }}</td>
            <td>{{ row.total }} / {{ row.variant_count }}</td>
            <td>{{ row.ready_count }} / {{ row.total }}</td>
            <td>
              <strong v-if="row.review_failed_count" style="color: #b84a49"
                >审核失败 · {{ row.review_failed_count }} 个 SKU</strong
              ><span v-else>{{ labels[row.status] }}</span
              ><small v-if="row.unsubmitted_count"> · {{ row.unsubmitted_count }} 个尚未提交</small>
            </td>
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
    <h3>{{ bulkMode === 'delete' ? '删除记录' : '恢复记录' }}</h3>
    <p>
      {{
        bulkMode === 'delete'
          ? `将所选 ${bulkIds.length} 条记录移到回收站，并停止对应的本地生成任务，无需先取消批次。商品资料和已分配编码会保留。包含已有沃尔玛提交记录的批次时，本次不会删除任何记录。`
          : `恢复所选 ${bulkIds.length} 条记录的列表显示；不会恢复上架任务。`
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
@media (max-width: 800px) {
  .page-heading {
    flex-direction: column;
    align-items: stretch;
  }
  .page-heading > .actions {
    flex-wrap: wrap;
  }
}
.batch-filters {
  display: grid;
  grid-template-columns:
    minmax(130px, 170px) minmax(130px, 170px) minmax(200px, 1fr) minmax(180px, 260px)
    auto;
  align-items: center;
  gap: 12px;
  margin: 20px 0;
  padding: 16px;
  border: 1px solid #dce5ee;
  border-radius: 14px;
  background: #f8fafc;
}
.batch-filters > * {
  min-width: 0;
}
.batch-filters :deep(.filter-select summary) {
  height: 42px;
  border-radius: 10px;
}
.batch-filters > .btn {
  min-height: 42px;
  padding-inline: 18px;
  border-radius: 10px;
}
.batch-search {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  padding: 0 12px;
  border: 1px solid #d6e2ed;
  border-radius: 10px;
  background: #fff;
  color: #7b91a6;
  transition:
    border-color 160ms ease,
    box-shadow 160ms ease;
}
.batch-search:focus-within {
  border-color: #89a6c2;
  box-shadow: 0 0 0 3px #6e94ba1a;
}
.batch-search input {
  flex: 1;
  width: 100%;
  min-width: 0;
  min-height: 40px;
  padding: 8px 0;
  border: 0;
  outline: none;
  box-shadow: none;
  background: transparent;
  color: #344b61;
  font-size: 14px;
  appearance: none;
}
.batch-search input::placeholder {
  color: #8a9aac;
}
.batch-search input::-webkit-search-cancel-button {
  appearance: none;
}
.batch-search-clear {
  display: grid;
  place-items: center;
  flex: 0 0 26px;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 0;
  border-radius: 7px;
  background: #edf3f8;
  color: #6b839a;
}
.batch-search-clear:hover {
  background: #dfeaf4;
  color: #345b80;
}
@media (max-width: 1000px) {
  .batch-filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 540px) {
  .batch-filters {
    padding: 12px;
    gap: 10px;
  }
  .batch-search {
    grid-column: 1 / -1;
    grid-row: 1;
  }
  .batch-time-filter {
    grid-column: 1 / -1;
  }
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
.batch-generate {
  background: #e7effa;
  border-color: #c2d4eb;
  color: #345b88;
}
.batch-validate {
  background: #e7f3ef;
  border-color: #c4dfd4;
  color: #2f6b59;
}
.batch-preparation {
  margin: 16px;
  padding: 18px;
  border: 1px solid #d7e3ee;
  border-radius: 12px;
  background: #f5f8fc;
}
.batch-preparation-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}
.batch-preparation-heading strong {
  font-size: 16px;
}
.batch-preparation-row {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) auto;
  gap: 8px 16px;
  margin-top: 12px;
  padding: 14px;
  border: 1px solid #e0e8f0;
  border-radius: 10px;
  background: #fff;
  overflow-wrap: anywhere;
}
.batch-preparation-row small {
  display: block;
  margin-top: 4px;
  color: var(--muted);
}
.batch-preparation-row p,
.batch-preparation-row details {
  grid-column: 1 / -1;
  margin: 0;
}
.batch-preparation-row summary {
  cursor: pointer;
  color: #466f96;
}
.batch-preparation-row ul {
  padding-left: 20px;
}
.batch-preparation-state {
  align-self: start;
  border-radius: 6px;
  padding: 4px 8px;
  background: #edf2f7;
  color: #4c6d8d;
  font-size: 13px;
}
.batch-preparation-state.complete {
  background: #e7f3ec;
  color: #347252;
}
.batch-preparation-state.failed,
.batch-preparation-state.review {
  background: #fceceb;
  color: #a64442;
}
@media (max-width: 540px) {
  .batch-preparation {
    margin: 10px;
    padding: 12px;
  }
  .batch-preparation-row {
    grid-template-columns: minmax(0, 1fr) auto;
  }
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
