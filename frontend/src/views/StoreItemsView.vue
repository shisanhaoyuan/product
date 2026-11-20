<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed, nextTick } from 'vue';
import { loadStoreItemImages, type StoreItemImage } from '../utils/storeItemImages';
import { request } from '../api/client';
import { errorMessage } from '../utils/display';
import ProductImage from '../components/ProductImage.vue';
import { safeUrl } from '../utils/display';
import { useAuth } from '../composables/useAuth';

interface Store {
  id: number;
  name: string;
  partner_id: string;
}
interface Item extends StoreItemImage {
  name: string;
  gtin: string;
  published: string;
  lifecycle: string;
  price: string | null;
  currency: string;
}
interface Page {
  items: Item[];
  cursor: string;
  total: number | null;
  partner: string;
  revision: number;
}
interface Node {
  id: string;
  name: string;
}
interface History {
  id: string;
  sku: string;
  kind: string;
  node: string;
  at: string;
  status: string;
  target: string;
  message: string;
}
const { auth } = useAuth();
const editable = computed(
  () => !auth.enabled || ['admin', 'editor'].includes(auth.user?.role || ''),
);
const stores = ref<Store[]>([]),
  sid = ref(''),
  search = ref(''),
  data = ref<Page>();
const histories = ref<History[]>([]),
  nodes = ref<Node[]>([]);
const busy = ref(false),
  error = ref(''),
  notice = ref('');
const cursor = ref('*'),
  pages = ref<string[]>([]);
const dialog = ref<HTMLDialogElement>();
const selection = ref<Item>(),
  kind = ref<'price' | 'stock' | 'retire'>('price');
const node = ref(''),
  expected = ref(''),
  target = ref(''),
  confirmSku = ref('');
const pendingRequest = ref<string>();
const labels: Record<string, string> = {
  price: '修改售价',
  stock: '调整库存',
  retire: '永久下架',
  pending: '结果待确认',
  verified: '平台已确认',
  failed: '未完成',
  PUBLISHED: '已发布',
  UNPUBLISHED: '未发布',
  SYSTEM_PROBLEM: '平台异常',
  READY_TO_PUBLISH: '等待发布',
  IN_PROGRESS: '处理中',
  STAGE: '暂存',
  ACTIVE: '有效',
  RETIRED: '已下架',
};
const post = (data: unknown) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(data),
});
const base = () => `/api/store-items/${sid.value}`;
let imageRequest: AbortController | undefined;
onBeforeUnmount(() => imageRequest?.abort());
async function history() {
  histories.value = await request<History[]>(base() + '/history');
}
async function load(reset = false) {
  if (!sid.value || busy.value) return;
  imageRequest?.abort();
  busy.value = true;
  error.value = '';
  selection.value = undefined;
  if (reset) {
    cursor.value = '*';
    pages.value = [];
  }
  try {
    const params = new URLSearchParams({ cursor: cursor.value, sku: search.value.trim() });
    data.value = await request<Page>(base() + '?' + params);
    imageRequest = new AbortController();
    void loadStoreItemImages(sid.value, data.value, imageRequest.signal);
    await history();
  } catch (e) {
    data.value = undefined;
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
async function changeStore() {
  data.value = undefined;
  histories.value = [];
  nodes.value = [];
  search.value = '';
  notice.value = '';
  await load(true);
}
async function next() {
  if (!data.value?.cursor) return;
  pages.value.push(cursor.value);
  cursor.value = data.value.cursor;
  await load();
}
async function edit(item: Item, action: typeof kind.value) {
  selection.value = item;
  kind.value = action;
  node.value = '';
  expected.value = '';
  target.value = '';
  confirmSku.value = '';
  pendingRequest.value = undefined;
  error.value = '';
  notice.value = '';
  await nextTick();
  dialog.value?.showModal();
  if (action === 'price') {
    expected.value = item.price || '';
    target.value = expected.value;
  } else if (action === 'retire') {
    expected.value = item.lifecycle;
    target.value = 'RETIRED';
  } else {
    busy.value = true;
    try {
      nodes.value = await request<Node[]>(base() + '/nodes');
    } catch (e) {
      error.value = errorMessage(e);
    } finally {
      busy.value = false;
    }
  }
}
async function readStock() {
  expected.value = '';
  target.value = '';
  pendingRequest.value = undefined;
  if (!node.value || !selection.value) return;
  busy.value = true;
  error.value = '';
  try {
    const value = await request<{ quantity: string }>(
      base() + '/stock?' + new URLSearchParams({ sku: selection.value.sku, node: node.value }),
    );
    expected.value = value.quantity;
    target.value = value.quantity;
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
async function save() {
  if (!selection.value || !data.value || busy.value) return;
  busy.value = true;
  error.value = '';
  pendingRequest.value ||= crypto.randomUUID();
  try {
    const result = await request<{ status: string; message: string }>(
      base() + '/changes',
      post({
        request_id: pendingRequest.value,
        partner: data.value.partner,
        revision: data.value.revision,
        sku: selection.value.sku,
        kind: kind.value,
        expected: expected.value,
        target: String(target.value),
        node: node.value,
        confirm_sku: confirmSku.value,
      }),
    );
    notice.value = result.message;
    selection.value = undefined;
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
  await history().catch(() => {});
  if (!selection.value) await load();
}
async function check(h: History) {
  busy.value = true;
  error.value = '';
  try {
    const result = await request<{ message: string }>(base() + `/changes/${h.id}/check`, post({}));
    notice.value = result.message;
    await history();
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
onMounted(async () => {
  try {
    stores.value = await request<Store[]>('/api/store-items');
    if (stores.value[0]) {
      sid.value = String(stores.value[0].id);
      await load(true);
    }
  } catch (e) {
    error.value = errorMessage(e);
  }
});
</script>
<template>
  <header class="page-heading">
    <div>
      <p class="eyebrow">WALMART / LIVE CATALOG</p>
      <h1>店铺商品管理</h1>
      <p class="subtitle">实时查询绑定店铺的沃尔玛商品，人工调整售价、分仓库存及下架状态。</p>
    </div>
    <RouterLink to="/listing-stores" class="btn">店铺管理</RouterLink>
  </header>
  <form class="live-filters" @submit.prevent="load(true)">
    <label
      >选择店铺<select v-model="sid" :disabled="busy" @change="changeStore">
        <option value="" disabled>请选择已绑定店铺</option>
        <option v-for="s in stores" :key="s.id" :value="String(s.id)">{{ s.name }}</option>
      </select></label
    >
    <label class="sku-search"
      >商品 SKU<input v-model="search" placeholder="输入完整 SKU 精确查询" :disabled="busy"
    /></label>
    <button class="btn primary" :disabled="busy || !sid">
      {{ busy ? '正在查询…' : '查询 / 刷新' }}
    </button>
  </form>
  <p v-if="!stores.length && !busy" class="notice">
    暂无已绑定并验证的店铺，请先到店铺管理完成绑定。
  </p>
  <p v-if="error" class="error-box" role="alert">{{ error }}</p>
  <p v-if="notice" class="notice" role="status">{{ notice }}</p>
  <section v-if="data" class="section">
    <div class="section-head">
      <h2>沃尔玛商品</h2>
      <span>本页 {{ data.items.length }} 件 · {{ data.total ?? '—' }} 件总记录</span>
    </div>
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th class="image-column">图片</th>
            <th>商品 / SKU</th>
            <th>售价</th>
            <th>平台状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in data.items" :key="item.sku">
            <td class="image-column">
              <a
                v-if="safeUrl(item.image)"
                :href="safeUrl(item.image)"
                target="_blank"
                rel="noopener noreferrer"
                class="offer-thumbnail"
                :aria-label="'查看商品图片：' + (item.name || item.sku)"
              >
                <ProductImage :src="item.image" :alt="item.name || item.sku" />
              </a>
              <div v-else class="offer-thumbnail">
                <ProductImage
                  :alt="item.name || item.sku"
                  :placeholder="
                    item.image_status === 'loading'
                      ? '正在获取图片…'
                      : item.image_status === 'unavailable'
                        ? '图片查询暂不可用'
                        : '平台暂未提供图片'
                  "
                />
              </div>
              <small v-if="item.image_source === 'submitted'" class="image-origin"
                >上架提交图</small
              >
            </td>
            <td class="live-product">
              <strong>{{ item.name || '平台未返回名称' }}</strong
              ><code>{{ item.sku }}</code
              ><small v-if="item.gtin">GTIN {{ item.gtin }}</small>
            </td>
            <td class="live-price">{{ item.currency }} {{ item.price ?? '未知' }}</td>
            <td>
              <span class="live-status" :class="{ retired: item.lifecycle === 'RETIRED' }">{{
                labels[item.published] || item.published || '未知'
              }}</span>
              <p class="muted">{{ labels[item.lifecycle] || item.lifecycle }}</p>
            </td>
            <td>
              <div class="live-actions">
                <button
                  class="btn small"
                  :disabled="
                    busy ||
                    !editable ||
                    item.lifecycle === 'RETIRED' ||
                    item.currency !== 'USD' ||
                    item.price === null
                  "
                  @click="edit(item, 'price')"
                >
                  修改售价
                </button>
                <button class="btn small" :disabled="busy" @click="edit(item, 'stock')">
                  {{ editable ? '库存 / 调整' : '查看库存' }}
                </button>
                <button
                  class="btn small retire"
                  :disabled="busy || !editable || item.lifecycle === 'RETIRED'"
                  @click="edit(item, 'retire')"
                >
                  下架
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="!data.items.length" class="empty">沃尔玛未返回匹配商品。</p>
    <div class="section-body actions">
      <button class="btn" :disabled="busy || !pages.length" @click="load(true)">返回首页</button>
      <span>第 {{ pages.length + 1 }} 页</span>
      <button class="btn" :disabled="busy || !data.cursor || !!search.trim()" @click="next">
        下一页
      </button>
    </div>
  </section>
  <dialog
    v-if="selection"
    ref="dialog"
    class="section edit-offer"
    aria-labelledby="edit-title"
    @cancel.prevent="!busy && (selection = undefined)"
  >
    <div class="section-head">
      <h2 id="edit-title">{{ labels[kind] }} · {{ selection.sku }}</h2>
      <button
        class="btn small"
        aria-label="关闭编辑"
        :disabled="busy"
        @click="selection = undefined"
      >
        ×
      </button>
    </div>
    <div class="section-body">
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
      <p>
        <strong>{{ stores.find((s) => String(s.id) === sid)?.name }}</strong> · {{ selection.name }}
      </p>
      <p v-if="kind === 'retire'" class="notice">
        下架会停用此 SKU，沃尔玛可能需要最多 48 小时处理。若只是暂时停售，可将对应仓库库存设为 0。
      </p>
      <div class="offer-fields">
        <label v-if="kind === 'stock'"
          >发货仓<select v-model="node" :disabled="busy" @change="readStock">
            <option value="">请选择仓库</option>
            <option v-for="n in nodes" :key="n.id" :value="n.id">{{ n.name }} · {{ n.id }}</option>
          </select></label
        >
        <p>
          沃尔玛当前值：<strong>{{ labels[expected] || expected || '请先读取' }}</strong>
        </p>
        <label v-if="kind !== 'retire'"
          >{{ kind === 'price' ? '新售价（USD）' : '新库存数量'
          }}<input
            :value="target"
            type="number"
            :min="kind === 'price' ? 0.01 : 0"
            :step="kind === 'price' ? 0.01 : 1"
            :disabled="busy || !editable"
            @input="
              target = ($event.target as HTMLInputElement).value;
              pendingRequest = undefined;
            "
        /></label>
        <label v-else>输入完整 SKU 确认下架<input v-model="confirmSku" :disabled="busy" /></label>
        <button
          v-if="editable"
          class="btn primary"
          :disabled="
            busy ||
            !expected ||
            !target ||
            selection.lifecycle === 'RETIRED' ||
            (kind === 'retire' && confirmSku !== selection.sku)
          "
          @click="save"
        >
          {{ busy ? '处理中…' : '确认提交到沃尔玛' }}
        </button>
      </div>
    </div>
  </dialog>
  <section v-if="histories.length" class="section offer-history">
    <div class="section-head">
      <h2>最近操作记录</h2>
      <span>当前店铺 · 最近 50 条</span>
    </div>
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>时间 / SKU</th>
            <th>操作与目标</th>
            <th>处理结果</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="h in histories" :key="h.id">
            <td>
              {{ new Date(h.at).toLocaleString('zh-CN') }}
              <p>
                <code>{{ h.sku }}</code>
              </p>
            </td>
            <td>
              {{ labels[h.kind] }} → {{ labels[h.target] || h.target }}
              <p class="muted">{{ h.node }}</p>
            </td>
            <td>
              {{ labels[h.status] || h.status }}
              <p class="history-message">{{ h.message }}</p>
            </td>
            <td>
              <button
                v-if="h.status === 'pending' && editable"
                class="btn small"
                :disabled="busy"
                @click="check(h)"
              >
                回读核对
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
<style scoped>
.live-filters {
  display: flex;
  gap: 16px;
  align-items: end;
  flex-wrap: wrap;
  background: #eaf0f6;
  border: 1px solid #d8e2ed;
  border-radius: 14px;
  padding: 20px;
  margin-bottom: 24px;
}
.live-filters label,
.offer-fields label {
  display: grid;
  gap: 8px;
  color: #526a83;
  font-weight: 600;
}
.live-filters select,
.live-filters input,
.offer-fields select,
.offer-fields input {
  padding: 11px 14px;
  background: white;
  border: 1px solid #cbd8e5;
  border-radius: 9px;
  min-width: 200px;
}
.sku-search {
  flex: 1;
}
.live-product {
  min-width: 240px;
  max-width: 440px;
  white-space: normal;
}
.live-product strong,
.live-product code,
.live-product small {
  display: block;
  margin-bottom: 7px;
  overflow-wrap: anywhere;
}
.live-product code,
.live-product small {
  color: var(--muted);
}
.live-price {
  font-size: 17px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}
.live-status {
  padding: 5px 10px;
  border-radius: 20px;
  background: #eaf1fa;
  color: #426488;
}
.live-status.retired {
  background: #f9e8e8;
  color: #9b4040;
}
.live-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.retire {
  color: #a74a4a;
  background: #fcf1f0;
}
.offer-history {
  margin-top: 24px;
}
.edit-offer {
  width: min(760px, calc(100vw - 32px));
  max-height: 85vh;
  overflow: auto;
  border: 1px solid #d8e2ed;
  border-radius: 16px;
  padding: 0;
  color: var(--ink);
}
.edit-offer::backdrop {
  background: #283a4d55;
}
.offer-fields {
  display: flex;
  align-items: end;
  flex-wrap: wrap;
  gap: 20px;
}
.history-message {
  white-space: normal;
  max-width: 420px;
  color: var(--muted);
}
@media (max-width: 700px) {
  .live-filters label,
  .live-filters button,
  .offer-fields label {
    width: 100%;
  }
}
</style>

<style scoped>
.edit-offer .section-head {
  padding: 20px 24px;
  margin: 0;
}
.edit-offer .section-body {
  padding: 20px 24px 28px;
}
</style>

<style scoped>
.image-column {
  width: 112px;
  min-width: 112px;
}
.offer-thumbnail {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 80px;
  height: 80px;
  border: 1px solid #dce4ec;
  border-radius: 10px;
  background: #fff;
  overflow: hidden;
}
a.offer-thumbnail:hover {
  border-color: #819fbd;
  box-shadow: 0 3px 12px #526c851a;
}
.offer-thumbnail :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.offer-thumbnail :deep(.image-placeholder) {
  width: 100%;
  height: 100%;
  font-size: 10px;
}
.image-origin {
  display: block;
  margin-top: 6px;
  color: var(--muted);
  font-size: 11px;
}
</style>
