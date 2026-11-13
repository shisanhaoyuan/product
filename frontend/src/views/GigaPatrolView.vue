<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { request } from '../api/client';
import { errorMessage } from '../utils/display';
interface Run {
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
  ok: '成功',
};
let controller: AbortController | undefined;
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
    const result = await request<Summary>('/api/integrations/giga/patrol', {
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
      `/api/integrations/giga/patrol/${run.id}?page=${targetPage}`,
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
onMounted(load);
onBeforeUnmount(() => {
  controller?.abort();
  detailController?.abort();
});
</script>
<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">GIGAB2B / MONITOR</p>
      <h1>价格与库存巡查</h1>
      <p class="subtitle">只检查当前大建云仓 API 账号已入库商品，保留每次巡查记录。</p>
    </div>
    <div class="actions">
      <RouterLink class="btn" to="/giga-products">返回大建云仓</RouterLink
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
      <p v-if="!data.configured" class="notice">尚未配置大建云仓 API，配置后才能巡查。</p>
      <p v-if="data.timer.next_run" class="subtitle">
        服务器报告下次运行：{{ data.timer.next_run }}
      </p>
      <p v-if="!data.timer.active" class="notice">
        部署后在服务器运行 deploy/install-giga-patrol.sh
        启用每周巡查。页面打开或关闭不影响服务器定时任务。
      </p>
      <p class="subtitle">
        更新原价、运费、币种、优惠价及可售库存；不乘倍率。失败项保留原值，手工编辑继续保留，最新来源值可在商品详情查看。
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
            <th>异常商品</th>
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
        变化按来源数据比较，不覆盖本地手工编辑。尚未检查
        {{ selected.total - selected.checked }} 件；每页最多 100 件。
      </p>
    </div>
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>供应商 SKU</th>
            <th>原价 · 前 → 后</th>
            <th>运费 · 前 → 后</th>
            <th>可售库存 · 前 → 后</th>
            <th>结果</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.listing_id">
            <td>
              <RouterLink class="detail-link" :to="'/giga-product/' + item.listing_id">{{
                item.sku
              }}</RouterLink>
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
.patrol-details {
  margin-top: 24px;
}
</style>
