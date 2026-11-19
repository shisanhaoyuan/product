<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { reviewApi, type ReviewPage, type ReviewRow } from '../api/review';
import { useAuth } from '../composables/useAuth';
import { errorMessage } from '../utils/display';
import FilterSelect from '../components/FilterSelect.vue';
import AppIcon from '../components/AppIcon.vue';
import ProductImage from '../components/ProductImage.vue';
import WalmartResultReasons from '../components/WalmartResultReasons.vue';
const { auth } = useAuth();
const canPoll = computed(() => !auth.enabled || auth.user?.role === 'admin');
const data = ref<ReviewPage>();
const page = ref(1),
  storeId = ref(''),
  status = ref(''),
  query = ref(''),
  history = ref(false);
const loading = ref(false),
  busy = ref(false),
  error = ref(''),
  pollError = ref(''),
  message = ref('');
const selected = ref<ReviewRow>(),
  detailDialog = ref<HTMLDialogElement>();
const labels: Record<string, string> = {
  processing: '平台处理中',
  published: '已发布',
  failed: '审核失败',
  ingested: '资料已接收，待发布',
  unpublished: '未发布',
  stock_pending: '库存待核验',
  unknown: '结果待确认',
  rejected: '提交被拒绝',
};
const pollingLabels: Record<string, string> = {
  pending: '后台持续查询中',
  running: '后台查询中',
  paused: '后台查询已暂停',
  complete: '后台任务已结束',
  repair_pending: '正在准备修复草稿',
};
const totalCount = computed(() =>
  Object.values(data.value?.counts || {}).reduce((a, b) => a + b, 0),
);
const date = (v: string) => (v ? new Date(v).toLocaleString('zh-CN') : '—');
const tone = (v: string) =>
  v === 'published'
    ? 'success'
    : ['failed', 'rejected', 'unpublished'].includes(v)
      ? 'danger'
      : ['stock_pending', 'unknown'].includes(v)
        ? 'warning'
        : 'pending';
let controller: AbortController | undefined,
  timer: ReturnType<typeof setInterval> | undefined,
  searchTimer: ReturnType<typeof setTimeout> | undefined;
let live = true;
async function load(quiet = false) {
  controller?.abort();
  const pending = new AbortController();
  controller = pending;
  if (!quiet) loading.value = true;
  error.value = '';
  try {
    const result = await reviewApi.list(
      new URLSearchParams({
        page: String(page.value),
        store_id: storeId.value || '0',
        status: status.value,
        q: query.value.trim(),
        history: String(history.value),
      }),
      pending.signal,
    );
    if (!live || pending.signal.aborted) return;
    const last = Math.max(1, Math.ceil(result.total / result.page_size));
    if (page.value > last) {
      page.value = last;
      await load(quiet);
      return;
    }
    data.value = result;
    const updated = result.items.find(
      (r) =>
        r.attempt_id === selected.value?.attempt_id &&
        r.platform_sku === selected.value?.platform_sku,
    );
    if (updated) selected.value = updated;
  } catch (e) {
    if (live && !pending.signal.aborted) error.value = errorMessage(e);
  } finally {
    if (live && !pending.signal.aborted) loading.value = false;
  }
}
function changePage(offset: number) {
  page.value += offset;
  void load();
}
async function open(row: ReviewRow) {
  selected.value = row;
  await nextTick();
  detailDialog.value?.showModal();
}
async function poll(rows: ReviewRow[]) {
  if (busy.value) return;
  const ids = [...new Set(rows.filter((r) => r.can_refresh).map((r) => r.attempt_id))];
  if (!ids.length) return;
  busy.value = true;
  pollError.value = '';
  message.value = '';
  let done = 0;
  const failures: string[] = [];
  for (const id of ids) {
    if (!live) break;
    try {
      const result = await reviewApi.refresh(id);
      done++;
      if (selected.value?.attempt_id === id) {
        const outcome = result.results[selected.value.platform_sku];
        if (outcome)
          selected.value = {
            ...selected.value,
            ...outcome,
            status_label: labels[outcome.status] || '结果待确认',
            updated_at: result.updated_at,
            stock_verified: outcome.stock_verified === true,
            published_status: outcome.published_status || '',
          };
      }
    } catch (e) {
      const row = rows.find((r) => r.attempt_id === id)!;
      failures.push(`${row.store_name} / ${row.feed_id}：${errorMessage(e)}`);
    }
  }
  if (live) {
    await load(true);
    pollError.value = failures.join('\n');
    message.value = `已查询 ${done} 个提交批次${failures.length ? `，${failures.length} 个查询失败，原结果保留` : ''}。`;
    busy.value = false;
  }
}
watch([storeId, status, history], () => {
  page.value = 1;
  void load();
});
watch(query, () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    page.value = 1;
    void load();
  }, 300);
});
onMounted(() => {
  void load();
  timer = setInterval(() => {
    if (!document.hidden && !busy.value && !loading.value) void load(true);
  }, 15000);
});
onBeforeUnmount(() => {
  live = false;
  controller?.abort();
  clearInterval(timer);
  clearTimeout(searchTimer);
});
</script>
<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">WALMART / SUBMISSION TRACKING</p>
      <h1>沃尔玛审核进度</h1>
      <p class="subtitle">提交后在此追踪进度，失败商品自动回到预上架处理。</p>
    </div>
    <RouterLink class="btn" to="/prelisting">预上架批次 <AppIcon name="ArrowRight" /></RouterLink>
  </div>
  <div class="review-counters" aria-label="提交状态统计">
    <button class="review-counter" :class="{ chosen: !status }" @click="status = ''">
      <span>{{ history ? '全部提交记录' : '已提交 SKU' }}</span
      ><strong>{{ totalCount }}</strong>
    </button>
    <button
      v-for="key in ['processing', 'published', 'failed', 'stock_pending', 'unknown']"
      :key="key"
      class="review-counter"
      :class="[tone(key), { chosen: status === key }]"
      @click="status = status === key ? '' : key"
    >
      <span>{{ labels[key] }}</span
      ><strong>{{ data?.counts[key] || 0 }}</strong>
    </button>
  </div>
  <section class="review-panel">
    <div class="review-toolbar">
      <FilterSelect
        v-model="storeId"
        label="审核店铺"
        :options="[
          { value: '', label: '全部店铺' },
          ...(data?.stores || []).map((s) => ({ value: String(s.id), label: s.name })),
        ]"
      />
      <FilterSelect
        v-model="status"
        label="审核状态"
        :options="[
          { value: '', label: '全部状态' },
          ...Object.entries(labels).map(([value, label]) => ({ value, label })),
        ]"
      />
      <input
        v-model="query"
        class="review-search"
        type="search"
        maxlength="200"
        aria-label="搜索审核记录"
        placeholder="SKU、来源 SKU、商品名、Feed ID"
      />
      <label class="review-history"><input v-model="history" type="checkbox" />包含历史重试</label>
    </div>
    <div class="review-actions">
      <span
        >{{ history ? '每次提交分别展示' : '同店铺、同账号、同 SKU 仅显示最新提交' }} · 共
        {{ data?.total || 0 }} 条</span
      >
      <div class="actions">
        <button class="btn small" :disabled="loading || busy" @click="load()">刷新记录</button
        ><button
          v-if="canPoll"
          class="btn primary small"
          :disabled="busy || loading || !data?.items.some((r) => r.can_refresh)"
          @click="poll(data?.items || [])"
        >
          {{ busy ? '正在查询平台…' : '查询本页平台进度' }}
        </button>
      </div>
    </div>
    <p class="review-note">
      记录每 15 秒自动刷新。后台任务负责查询平台；管理员也可手动查询。Feed ID
      仅表示平台接收，只有“已发布”代表发布成功。
    </p>
    <p v-if="message" class="notice" role="status">{{ message }}</p>
    <p v-if="error || pollError" class="error-box review-error" role="alert">
      {{ [error, pollError].filter(Boolean).join('\n') }}
    </p>
    <div v-if="loading && !data" class="loading">正在加载提交记录…</div>
    <div class="review-table-wrap" :aria-busy="loading">
      <table class="review-table">
        <thead>
          <tr>
            <th>商品 / SKU</th>
            <th>目标店铺</th>
            <th>审核进度</th>
            <th>提交与更新时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in data?.items" :key="`${row.attempt_id}:${row.platform_sku}`">
            <td class="review-product">
              <div class="review-product-summary">
                <button
                  class="review-product-photo"
                  :aria-label="'查看商品审核详情 ' + row.source_sku"
                  @click="open(row)"
                >
                  <ProductImage :src="row.image_url" :alt="row.title || row.source_sku" />
                </button>
                <div class="review-product-copy">
                  <strong>{{ row.title || row.source_sku }}</strong
                  ><code>{{ row.platform_sku }}</code
                  ><small>来源 SKU：{{ row.source_sku }}</small
                  ><small v-if="!row.is_latest">历史提交，已有较新结果</small>
                </div>
              </div>
            </td>
            <td>
              <strong>{{ row.store_name }}</strong
              ><small>{{ row.brand || '未记录品牌' }}</small
              ><small>Partner：{{ row.partner_id }}</small>
            </td>
            <td class="review-outcome">
              <span class="review-status" :class="tone(row.status)">{{ row.status_label }}</span>
              <p>{{ row.message }}</p>
              <WalmartResultReasons :errors="row.errors" />
              <small v-if="!row.binding_matches" class="warning">店铺绑定已变化，查询已保护</small
              ><small v-else-if="row.last_error" class="warning"
                >后台查询提示：{{ row.last_error }}</small
              >
            </td>
            <td class="review-time">
              <span>{{ date(row.created_at) }}</span
              ><small>结果更新 {{ date(row.updated_at) }}</small
              ><code class="review-feed">Feed：{{ row.feed_id || '尚未取得编号' }}</code>
            </td>
            <td>
              <div class="review-row-actions">
                <button class="btn small" @click="open(row)">查看详情</button
                ><button
                  v-if="canPoll"
                  class="btn small"
                  :disabled="busy || !row.can_refresh"
                  @click="poll([row])"
                >
                  查询进度</button
                ><RouterLink
                  class="detail-link"
                  :to="{
                    path: `/prelisting/${row.batch_id}`,
                    query: { sku: row.platform_sku, view: 'results' },
                  }"
                  >查看批次 ↗</RouterLink
                >
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="data && !data.total" class="review-empty">
        <AppIcon name="ScanLine" />
        <h2>
          {{ query || status || storeId ? '没有符合条件的提交记录' : '还没有提交到沃尔玛的商品' }}
        </h2>
        <p>预上架草稿不会出现在这里。完成资料校验并提交沃尔玛后，即可在此追踪进度。</p>
      </div>
    </div>
    <div class="review-pagination">
      <span
        >第 {{ page }} /
        {{ Math.max(1, Math.ceil((data?.total || 0) / (data?.page_size || 20))) }} 页</span
      ><button class="btn small" :disabled="page <= 1 || loading || busy" @click="changePage(-1)">
        上一页</button
      ><button
        class="btn small"
        :disabled="!data || page * data.page_size >= data.total || loading || busy"
        @click="changePage(1)"
      >
        下一页
      </button>
    </div>
  </section>
  <dialog
    ref="detailDialog"
    class="review-dialog"
    aria-labelledby="review-detail-title"
    @click="$event.target === detailDialog && detailDialog?.close()"
  >
    <template v-if="selected">
      <div class="review-dialog-heading">
        <div>
          <p class="eyebrow">SUBMISSION DETAIL</p>
          <h2 id="review-detail-title">商品审核详情</h2>
        </div>
        <button class="btn small" aria-label="关闭审核详情" @click="detailDialog?.close()">
          <AppIcon name="X" />
        </button>
      </div>
      <div class="review-product-summary review-detail-product">
        <div class="review-product-photo">
          <ProductImage :src="selected.image_url" :alt="selected.title || selected.source_sku" />
        </div>
        <h3>{{ selected.title || selected.source_sku }}</h3>
      </div>
      <span class="review-status" :class="tone(selected.status)">{{ selected.status_label }}</span>
      <p>{{ selected.message }}</p>
      <dl class="review-facts">
        <dt>目标店铺</dt>
        <dd>
          {{ selected.store_name }} · {{ selected.brand }} · Partner {{ selected.partner_id }}
        </dd>
        <dt>平台 SKU</dt>
        <dd>{{ selected.platform_sku }}</dd>
        <dt>来源 SKU</dt>
        <dd>{{ selected.source_sku }}</dd>
        <dt>Feed ID</dt>
        <dd>{{ selected.feed_id || '未取得，请在原批次中核对并关联' }}</dd>
        <dt>本地批次</dt>
        <dd>{{ selected.batch_id }}</dd>
        <dt>提交时间</dt>
        <dd>{{ date(selected.created_at) }}</dd>
        <dt>结果更新时间</dt>
        <dd>{{ date(selected.updated_at) }}</dd>
        <dt>库存核验</dt>
        <dd>{{ selected.stock_verified ? '已通过' : '尚未通过，进度查询不会修改库存' }}</dd>
        <dt>后台任务</dt>
        <dd>
          {{ pollingLabels[selected.polling_state || ''] || '未安排后台查询，可手动查询进度' }}
        </dd>
      </dl>
      <p v-if="selected.last_error" class="error-box">{{ selected.last_error }}</p>
      <p v-if="!selected.binding_matches" class="error-box">
        当前店铺绑定与提交账号不一致或不可用，请先在店铺管理核对绑定。
      </p>
      <div v-if="selected.errors.length" class="review-reasons">
        <h3>需要处理的问题</h3>
        <WalmartResultReasons :errors="selected.errors" />
      </div>
      <p v-if="pollError" class="error-box review-error">{{ pollError }}</p>
      <div class="review-dialog-footer">
        <RouterLink
          class="btn"
          :to="{ path: `/prelisting/${selected.batch_id}`, query: { sku: selected.platform_sku } }"
          >进入批次查看 / 修改资料</RouterLink
        ><button
          v-if="canPoll"
          class="btn primary"
          :disabled="busy || !selected.can_refresh"
          @click="poll([selected])"
        >
          {{ busy ? '查询中…' : '查询平台进度' }}
        </button>
      </div>
    </template>
  </dialog>
</template>
<style scoped>
.review-counters {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
  margin: 24px 0;
}
.review-counter {
  text-align: left;
  border: 1px solid #dce3eb;
  border-radius: 14px;
  background: #fff;
  padding: 18px;
  color: #53657a;
  cursor: pointer;
}
.review-counter span {
  display: block;
  font-size: 14px;
}
.review-counter strong {
  display: block;
  font-size: 30px;
  line-height: 1.2;
  margin-top: 12px;
  font-variant-numeric: tabular-nums;
}
.review-counter.chosen {
  border-color: #7593b7;
  box-shadow: 0 0 0 2px #7593b71a;
  background: #f3f7fc;
}
.review-panel {
  background: #fff;
  border: 1px solid #dce3eb;
  border-radius: 16px;
  overflow: hidden;
}
.review-toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  padding: 20px 20px 12px;
}
.review-search {
  flex: 1;
  min-width: 220px;
  padding: 10px 12px;
  border: 1px solid #dce3eb;
  border-radius: 9px;
  background: #fafbfd;
  color: #34485e;
}
.review-history {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  white-space: nowrap;
}
.review-history input {
  width: 16px;
  height: 16px;
}
.review-actions {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 0 20px 12px;
  font-size: 13px;
  color: #63758a;
}
.review-note {
  font-size: 13px;
  color: #63758a;
  margin: 0;
  padding: 12px 20px;
  background: #f6f8fb;
  border-block: 1px solid #e5eaf0;
}
.review-table-wrap {
  overflow: auto;
}
.review-table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
}
.review-table th {
  background: #fafbfd;
  font-size: 13px;
  color: #6a7c91;
  padding: 14px 18px;
  white-space: nowrap;
}
.review-table td {
  padding: 20px 18px;
  border-top: 1px solid #e7ecf2;
  vertical-align: top;
  font-size: 14px;
}
.review-table td strong,
.review-table td small,
.review-product code {
  display: block;
}
.review-table td small {
  font-size: 12px;
  line-height: 1.6;
  margin-top: 6px;
  color: #6a7c91;
}
.review-product {
  min-width: 310px;
  max-width: 450px;
  overflow-wrap: anywhere;
}
.review-product-summary {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}
.review-product-photo {
  flex: 0 0 84px;
  width: 84px;
  height: 84px;
  padding: 5px;
  overflow: hidden;
  border: 1px solid #dce5ee;
  border-radius: 12px;
  background: #f7f9fc;
}
button.review-product-photo:hover {
  border-color: #8da9c5;
  background: #edf3f9;
}
.review-product-photo :deep(img) {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
  border-radius: 7px;
}
.review-product-photo :deep(.image-placeholder) {
  width: 100%;
  height: 100%;
  min-height: 0;
  padding: 4px;
  font-size: 10px;
  text-align: center;
}
.review-product-copy {
  min-width: 0;
  flex: 1;
}
.review-detail-product {
  margin: 20px 0;
  align-items: center;
}
.review-detail-product h3 {
  margin: 0;
  overflow-wrap: anywhere;
}
.review-product strong {
  line-height: 1.6;
  margin-bottom: 8px;
}
.review-product code {
  color: #456281;
  font-size: 13px;
}
.review-outcome {
  min-width: 220px;
  max-width: 340px;
}
.review-outcome p {
  margin: 9px 0 0;
  color: #53657a;
  font-size: 13px;
  line-height: 1.7;
}
.review-status {
  display: inline-flex;
  align-items: center;
  padding: 5px 10px;
  border-radius: 7px;
  background: #edf3fa;
  font-size: 13px;
  font-weight: 600;
  color: #49698e;
}
.success {
  color: #237154 !important;
}
.danger {
  color: #b84a49 !important;
}
.warning {
  color: #9b6a20 !important;
}
.review-status.success {
  background: #eaf6ef;
}
.review-status.danger {
  background: #fcefee;
}
.review-status.warning {
  background: #fff5e4;
}
.review-time {
  min-width: 190px;
}
.review-feed {
  display: block;
  font-size: 11px;
  color: #6a7c91;
  margin-top: 9px;
  max-width: 220px;
  overflow-wrap: anywhere;
}
.review-row-actions {
  display: grid;
  gap: 8px;
  min-width: 88px;
}
.review-row-actions .detail-link {
  text-align: center;
  font-size: 12px;
}
.review-pagination {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  align-items: center;
  border-top: 1px solid #e7ecf2;
  padding: 16px 20px;
  font-size: 13px;
}
.review-pagination span {
  margin-right: auto;
  color: #6a7c91;
}
.review-empty {
  text-align: center;
  color: #6a7c91;
  padding: 60px 24px;
}
.review-empty h2 {
  font-size: 18px;
  color: #42586e;
}
.review-empty .icon {
  width: 32px;
  height: 32px;
}
.review-error {
  white-space: pre-line;
}
.review-panel > .error-box,
.review-panel > .notice {
  margin: 12px 20px;
}
.review-dialog {
  width: min(760px, calc(100vw - 32px));
  max-height: 90vh;
  border: 1px solid #dce3eb;
  border-radius: 18px;
  padding: 28px;
  color: #34485e;
  box-sizing: border-box;
}
.review-dialog::backdrop {
  background: #20344a66;
  backdrop-filter: blur(4px);
}
.review-dialog-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}
.review-dialog-heading h2 {
  margin: 4px 0 20px;
}
.review-dialog h3 {
  font-size: 16px;
  line-height: 1.6;
}
.review-dialog p {
  line-height: 1.7;
}
.review-facts {
  display: grid;
  grid-template-columns: 110px 1fr;
  font-size: 14px;
  gap: 12px;
  margin: 24px 0;
}
.review-facts dt {
  color: #718298;
}
.review-facts dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.review-reasons {
  border: 1px solid #ecd5d4;
  background: #fff8f7;
  border-radius: 12px;
  padding: 16px;
}
.review-reasons ul {
  padding-left: 20px;
  line-height: 1.8;
}
.review-reasons pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
}
.review-dialog-footer {
  display: flex;
  gap: 12px;
  justify-content: flex-end;
  margin-top: 24px;
}
@media (max-width: 1100px) {
  .review-counters {
    grid-template-columns: repeat(3, 1fr);
  }
}
@media (max-width: 640px) {
  .review-counters {
    grid-template-columns: repeat(2, 1fr);
    gap: 8px;
  }
  .review-toolbar,
  .review-actions {
    align-items: stretch;
    flex-direction: column;
  }
  .review-dialog {
    padding: 20px;
  }
  .review-dialog-footer {
    flex-direction: column;
  }
}
</style>
