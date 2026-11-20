<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { request } from '../api/client';
import { errorMessage } from '../utils/display';
const route = useRoute();
const provider = () => route.path === '/saleyee-patrol' ? 'saleyee' : 'giga';
const supplier = () => provider() === 'saleyee' ? '赛盈' : '大建云仓';
interface Action {
  id: string;
  store_name: string;
  source_quote?: string[] | null;
  submitted_quote?: string[] | null;
  platform_sku: string;
  kind: string;
  status: string;
  multiplier: string | null;
  source_price: string | null;
  source_shipping: string | null;
  source_stock: string | null;
  landed: string | null;
  target: string | null;
  before: string | null;
  after: string | null;
  node: string | null;
  message: string;
  batch_id: string;
  updated_at: string;
}
interface Run {
  actions: Record<string, number>;
  id: string;
  started_at: string;
  finished_at: string;
  status: string;
  total: number;
  checked: number;
  changed: number;
  failed: number;
  price_changed: number;
  stock_changed: number;
  message: string;
}
interface Values {
  quote?: string[] | null;
  price?: string | null;
  shipping?: string | null;
  currency?: string | null;
  stock?: string | null;
}
interface Item {
  listing_id: string;
  sku: string;
  checked_at: string;
  status: string;
  before: Values;
  after: Values;
  errors: string[];
  actions: Action[];
}
interface Summary {
  configured: boolean;
  timer: { schedule: string; installed: boolean; active: boolean; next_run?: string };
  runs: Run[];
}
const data = ref<Summary>(),
  selected = ref<Run>(),
  items = ref<Item[]>([]);
const loading = ref(false),
  error = ref(''),
  page = ref(1);
const labels: Record<string, string> = {
  running: '巡查中',
  completed: '巡查完成',
  partial: '部分失败',
  failed: '巡查失败',
  interrupted: '巡查中断',
  ok: '来源核验成功',
  queued: '等待更新',
  sending: '提交中／待回读',
  verified: '已回读确认',
  unchanged: '无需修改',
  skipped: '未触发更新',
  pending: '结果待确认',
  blocked: '等待上次结果',
};
let controller: AbortController | undefined;
const issues = (run: Run) =>
  ['failed', 'pending', 'blocked', 'sending', 'queued'].reduce(
    (n, key) => n + (run.actions?.[key] || 0),
    0,
  );
const date = (v: string) => (v ? new Date(v).toLocaleString('zh-CN') : '—');
const amount = (v: string | null | undefined, currency?: string | null) =>
  v == null ? '未知' : `${currency || ''} ${v}`.trim();
async function load() {
  controller?.abort();
  const pending = new AbortController();
  controller = pending;
  loading.value = true;
  error.value = '';
  try {
    const result = await request<Summary>(`/api/integrations/${provider()}/patrol`, {
      signal: pending.signal,
    });
    if (pending.signal.aborted) return;
    data.value = result;
    if (selected.value) {
      const current = result.runs.find((r) => r.id === selected.value?.id);
      if (current) await open(current, page.value);
    }
  } catch (e) {
    if (!pending.signal.aborted) error.value = errorMessage(e);
  } finally {
    if (!pending.signal.aborted) loading.value = false;
  }
}
let detailController: AbortController | undefined;
async function open(run: Run, targetPage = 1) {
  detailController?.abort();
  const pending = new AbortController();
  detailController = pending;
  selected.value = run;
  items.value = [];
  error.value = '';
  try {
    const result = await request<{ run: Run; items: Item[]; page: number }>(
      `/api/integrations/${provider()}/patrol/${run.id}?page=${targetPage}`,
      { signal: pending.signal },
    );
    if (!pending.signal.aborted) {
      selected.value = result.run;
      items.value = result.items;
      page.value = result.page;
    }
  } catch (e) {
    if (!pending.signal.aborted) error.value = errorMessage(e);
  }
}
watch(() => route.path, () => {
  detailController?.abort();
  selected.value = undefined;
  items.value = [];
  data.value = undefined;
  page.value = 1;
  load();
}, { immediate: true });
onBeforeUnmount(() => {
  controller?.abort();
  detailController?.abort();
});
</script>
<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">{{ provider() === 'saleyee' ? 'SALEYEE' : 'GIGAB2B' }} / MONITOR</p>
      <h1>价格与库存巡查</h1>
      <p class="subtitle">只检查当前{{ supplier() }} API 账号已入库商品，保留每次巡查记录。</p>
    </div>
    <div class="actions">
      <RouterLink class="btn" :to="`/${provider()}-products`">返回{{ supplier() }}</RouterLink
      ><button class="btn primary" :disabled="loading" @click="load">
        {{ loading ? '刷新中…' : '刷新记录' }}
      </button>
    </div>
  </div>
  <p v-if="error" class="error-box" role="alert">{{ error }}</p>
  <section v-if="data" class="section">
    <div class="section-head">
      <h2>{{ data.timer.schedule }}</h2>
      <span>{{ data.timer.active ? '定时巡查已启动' : '服务器定时巡查未启动' }}</span>
    </div>
    <div class="section-body">
      <p v-if="!data.configured" class="notice">尚未配置{{ supplier() }} API，配置后才能巡查。</p>
      <p v-if="data.timer.next_run" class="subtitle">
        服务器报告下次运行：{{ data.timer.next_run }}
      </p>
      <p v-if="!data.timer.active" class="notice">
        部署后在服务器运行 deploy/install-giga-patrol.sh
        启用每周巡查。页面打开或关闭不影响服务器定时任务。
      </p>
      <p class="subtitle">
        巡查来源价格和库存后，自动更新已上架的对应店铺商品：成本上涨时按{{ provider() === 'saleyee' ? '采购价×原上架倍率（不另加运费）' : '（单价＋运费）×原上架倍率' }}计算，仅上调售价；供应商明确无库存时清零。不重新上架，不自动降价或补库存。
      </p>
    </div>
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>巡查时间</th>
            <th>状态</th>
            <th>已检查 / 总数</th>
            <th>价格变化</th>
            <th>库存变化</th>
            <th>来源异常</th>
            <th>店铺更新 · 确认 / 待处理</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in data.runs" :key="run.id">
            <td>{{ date(run.started_at) }}</td>
            <td>{{ labels[run.status] || run.status }}</td>
            <td>{{ run.checked }} / {{ run.total }}</td>
            <td>{{ run.price_changed }}</td>
            <td>{{ run.stock_changed }}</td>
            <td>{{ run.failed }}</td>
            <td>{{ run.actions?.verified || 0 }} / {{ issues(run) }}</td>
            <td><button class="btn small" @click="open(run)">查看明细</button></td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="!data.runs.length" class="empty">暂无巡查记录</p>
  </section>
  <section v-if="selected" class="section patrol-details">
    <div class="section-head">
      <h2>{{ date(selected.started_at) }} · 巡查明细</h2>
      <span>{{ labels[selected.status] }}</span>
    </div>
    <div class="section-body">
      <p v-if="selected.message" class="notice">{{ selected.message }}</p>
      <p class="subtitle">
        来源变化与店铺修改分别统计；店铺更新按“店铺 × SKU ×
        价格或库存”计数。只有回读一致才显示已确认。尚未检查
        {{ selected.total - selected.checked }} 件；每页最多 100 件。
      </p>
    </div>
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>供应商 SKU</th>
            <th>采购价 · 前 → 后</th>
            <th>运费 · 前 → 后</th>
            <th>可售库存 · 前 → 后</th>
            <th>结果</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="item in items" :key="item.listing_id">
            <tr>
              <td>
                <RouterLink class="detail-link" :to="`/${provider()}-product/${item.listing_id}`">{{
                  item.sku
                }}</RouterLink>
                <p v-if="item.after.quote" class="quote-info">
                  站点 {{ item.after.quote[0] }} · 仓库 {{ item.after.quote[1] }} · 物流 {{ item.after.quote[2] }}
                </p>
              </td>
              <td>
                {{ amount(item.before.price, item.before.currency) }} →
                {{ amount(item.after.price, item.after.currency) }}
              </td>
              <td>
                {{ amount(item.before.shipping, item.before.currency) }} →
                {{ amount(item.after.shipping, item.after.currency) }}
              </td>
              <td>{{ item.before.stock ?? '未知' }} → {{ item.after.stock ?? '未知' }}</td>
              <td>
                {{ labels[item.status] }}
                <p v-for="reason in item.errors" :key="reason">{{ reason }}</p>
              </td>
            </tr>
            <tr class="store-actions-row">
              <td colspan="5">
                <div v-if="item.actions?.length" class="store-actions">
                  <article v-for="action in item.actions" :key="action.id" class="store-action">
                    <header>
                      <strong
                        >{{ action.store_name }} ·
                        {{ action.kind === 'price' ? '售价同步' : '库存清零' }}</strong
                      >
                      <span class="action-status" :class="action.status">{{
                        labels[action.status] || action.status
                      }}</span>
                    </header>
                    <p class="sku-line">
                      平台 SKU：<code>{{ action.platform_sku }}</code
                      ><span v-if="action.node"> · 仓库 {{ action.node }}</span>
                    </p>
                    <p v-if="action.submitted_quote" class="quote-info">
                      原上架来源：{{ action.submitted_quote.join(' / ') }}
                    </p>
                    <dl>
                      <template v-if="action.kind === 'price'">
                        <div>
                          <dt>本次计算</dt>
                          <dd>
                            （{{ action.source_price ?? '未知' }} ＋
                            {{ action.source_shipping ?? '未知' }}）×
                            {{ action.multiplier ?? '未知' }}
                          </dd>
                        </div>
                        <div>
                          <dt>目标售价（USD）</dt>
                          <dd>{{ action.target ?? '未触发' }}</dd>
                        </div>
                      </template>
                      <template v-else>
                        <div>
                          <dt>供应商可售库存</dt>
                          <dd>{{ action.source_stock ?? '未知' }}</dd>
                        </div>
                        <div>
                          <dt>目标库存</dt>
                          <dd>{{ action.target ?? '未触发' }}</dd>
                        </div>
                      </template>
                      <div>
                        <dt>沃尔玛修改前</dt>
                        <dd>{{ action.before ?? '未读取' }}</dd>
                      </div>
                      <div>
                        <dt>沃尔玛回读值</dt>
                        <dd>{{ action.after ?? '未确认' }}</dd>
                      </div>
                    </dl>
                    <p class="action-message">{{ action.message }}</p>
                    <footer>{{ date(action.updated_at) }}</footer>
                  </article>
                </div>
                <p v-else class="no-store-actions">
                  本条记录没有店铺更新任务（无已确认上架的对应商品，或为旧版巡查记录）。
                </p>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
    <div class="section-body actions">
      <button class="btn" :disabled="page <= 1" @click="open(selected, page - 1)">上一页</button
      ><span>{{ page }}</span
      ><button
        class="btn"
        :disabled="page * 100 >= selected.checked"
        @click="open(selected, page + 1)"
      >
        下一页
      </button>
    </div>
  </section>
</template>
<style scoped>
.quote-info {
  font-size: 12px;
  color: #64748b;
  white-space: normal;
  overflow-wrap: anywhere;
  margin: 6px 0;
}
.patrol-details {
  margin-top: 24px;
}
.store-actions-row > td {
  padding: 0 16px 22px;
  background: #f7f9fb;
}
.store-actions {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 380px), 1fr));
  gap: 12px;
  padding-top: 12px;
  white-space: normal;
}
.store-action {
  padding: 16px;
  background: #fff;
  border: 1px solid #dce4eb;
  border-radius: 12px;
  font-size: 14px;
}
.store-action header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.action-status {
  border-radius: 16px;
  background: #edf2f7;
  padding: 4px 10px;
  font-size: 12px;
}
.action-status.verified,
.action-status.unchanged {
  color: #176341;
  background: #e8f5ed;
}
.action-status.pending,
.action-status.sending,
.action-status.blocked,
.action-status.queued {
  color: #86530e;
  background: #fff3d9;
}
.action-status.failed {
  color: #a32727;
  background: #ffeded;
}
.sku-line,
.store-action footer {
  color: #64748b;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.store-action dl {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 16px 0;
}
.store-action dt {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 4px;
}
.store-action dd {
  margin: 0;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.action-message {
  line-height: 1.6;
  margin-bottom: 10px;
}
.no-store-actions {
  color: #64748b;
  font-size: 13px;
  margin: 12px 0 0;
  white-space: normal;
}
</style>
