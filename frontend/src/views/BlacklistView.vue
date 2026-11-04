<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { catalogApi } from '../api/client';
import type { SellerBlock, SellerBlockPage } from '../api/types';
import { useAuth } from '../composables/useAuth';
import { errorMessage } from '../utils/display';
import StatusBadge from '../components/StatusBadge.vue';
const route = useRoute();
const { auth } = useAuth();
const canEdit = computed(
  () => !auth.enabled || ['admin', 'editor'].includes(auth.user?.role || ''),
);
const data = ref<SellerBlockPage>();
const loading = ref(false),
  busy = ref(false),
  error = ref(''),
  message = ref('');
const q = ref(''),
  status = ref('active'),
  page = ref(1);
const source = ref('Amazon'),
  seller = ref(''),
  reason = ref('');
const editing = ref<SellerBlock>();
let controller: AbortController | undefined;
function resetForm() {
  editing.value = undefined;
  source.value = 'Amazon';
  seller.value = '';
  reason.value = '';
}
function edit(entry: SellerBlock) {
  editing.value = entry;
  source.value = entry.source;
  seller.value = entry.seller;
  reason.value = entry.reason;
  message.value = '';
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
async function load() {
  controller?.abort();
  const current = new AbortController();
  controller = current;
  loading.value = true;
  error.value = '';
  try {
    const result = await catalogApi.blacklist(
      new URLSearchParams({
        q: q.value.trim(),
        status: status.value,
        page: String(page.value),
      }).toString(),
      current.signal,
    );
    if (!current.signal.aborted) data.value = result;
  } catch (e) {
    if (!current.signal.aborted) error.value = errorMessage(e);
  } finally {
    if (!current.signal.aborted) loading.value = false;
  }
}
async function save() {
  busy.value = true;
  error.value = '';
  message.value = '';
  try {
    if (editing.value) {
      await catalogApi.updateSellerBlock(editing.value.id, {
        reason: reason.value.trim(),
        active: !!editing.value.active,
      });
      message.value = '拉黑原因已保存';
    } else {
      await catalogApi.blockSeller({
        source: source.value.trim(),
        seller: seller.value.trim(),
        reason: reason.value.trim(),
      });
      message.value = '卖家已加入黑名单，相关商品已同步标记';
      q.value = seller.value.trim();
      status.value = 'active';
    }
    resetForm();
    page.value = 1;
    await load();
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
async function toggle(entry: SellerBlock) {
  busy.value = true;
  error.value = '';
  message.value = '';
  try {
    await catalogApi.updateSellerBlock(entry.id, { reason: entry.reason, active: !entry.active });
    message.value = entry.active ? '已解除拉黑，原因记录保留' : '已重新拉黑';
    if (editing.value?.id === entry.id) resetForm();
    page.value = 1;
    await load();
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
function search() {
  page.value = 1;
  void load();
}
function next(offset: number) {
  page.value += offset;
  void load();
}
watch(
  () => route.fullPath,
  () => {
    resetForm();
    message.value = '';
    page.value = 1;
    source.value = typeof route.query.source === 'string' ? route.query.source : 'Amazon';
    seller.value = typeof route.query.seller === 'string' ? route.query.seller : '';
    q.value = typeof route.query.q === 'string' ? route.query.q : seller.value;
    status.value = 'all';
    void load();
  },
  { immediate: true },
);
onBeforeUnmount(() => controller?.abort());
</script>
<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">SELLER / 风险管理</p>
      <h1>卖家黑名单</h1>
      <p class="subtitle">记录需要避开的卖家，让选品判断有据可查。</p>
    </div>
    <RouterLink class="btn" to="/products?seller_status=blocked">查看命中商品 ↗</RouterLink>
  </div>
  <div class="notice notice-space">
    同平台、同名称的卖家跨店铺匹配，忽略大小写及多余空格。当前按名称识别，同名卖家请核实。仅标记本商品库，不会操作
    ERP 或 Amazon。
  </div>
  <section v-if="canEdit" class="section seller-section">
    <div class="section-head">
      <h2>{{ editing ? '修改拉黑原因' : '添加拉黑卖家' }}</h2>
      <button v-if="editing" class="btn small" :disabled="busy" @click="resetForm">取消编辑</button>
    </div>
    <form class="section-body blacklist-form" @submit.prevent="save">
      <label
        >来源平台<input
          v-model="source"
          required
          maxlength="100"
          placeholder="例如 Amazon"
          :disabled="busy || !!editing"
      /></label>
      <label
        >卖家名称<input
          v-model="seller"
          required
          maxlength="300"
          placeholder="填写准确的卖家名称"
          :disabled="busy || !!editing"
      /></label>
      <label class="reason-field"
        >拉黑原因<textarea
          v-model="reason"
          required
          maxlength="2000"
          rows="3"
          placeholder="例如：多次缺货、发货延迟、商品质量问题…"
          :disabled="busy"
        ></textarea
        ><span class="muted">{{ reason.length }} / 2000</span></label
      >
      <div class="actions">
        <button
          class="btn primary"
          :disabled="busy || !source.trim() || !seller.trim() || !reason.trim()"
        >
          {{ busy ? '正在保存…' : editing ? '保存原因' : '加入黑名单' }}</button
        ><span v-if="editing && !editing.active" class="muted"
          >此记录已解除，修改原因不会重新拉黑。</span
        >
      </div>
    </form>
  </section>
  <p v-else class="notice notice-space">当前账号为只读成员，可查看黑名单与原因。</p>
  <p v-if="message" role="status" class="notice notice-space">{{ message }}</p>
  <div v-if="error" role="alert" class="error-box">{{ error }}</div>
  <section class="section">
    <div class="section-head">
      <h2>
        卖家名单 <span class="muted">{{ data?.total ?? '—' }}</span>
      </h2>
    </div>
    <form class="filter-bar blacklist-search" @submit.prevent="search">
      <input
        v-model="q"
        type="search"
        aria-label="搜索黑名单"
        placeholder="搜索卖家、平台或拉黑原因"
        :disabled="busy"
      />
      <select v-model="status" aria-label="黑名单状态" :disabled="busy" @change="search">
        <option value="all">全部记录</option>
        <option value="active">已拉黑</option>
        <option value="inactive">已解除</option>
      </select>
      <button class="btn" :disabled="busy">搜索</button>
    </form>
    <div v-if="loading" class="loading">正在读取名单…</div>
    <template v-else-if="data?.items.length">
      <div class="table-scroll">
        <table class="data-table blacklist-table">
          <thead>
            <tr>
              <th>卖家 / 来源</th>
              <th>拉黑原因</th>
              <th>状态</th>
              <th>更新时间</th>
              <th v-if="canEdit">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in data.items" :key="entry.id">
              <td>
                <strong>{{ entry.seller }}</strong>
                <p class="muted">{{ entry.source }}</p>
              </td>
              <td class="blacklist-reason">{{ entry.reason }}</td>
              <td>
                <StatusBadge :type="entry.active ? 'warn' : 'good'">{{
                  entry.active ? '已拉黑' : '已解除'
                }}</StatusBadge>
              </td>
              <td>{{ new Date(entry.updated_at).toLocaleString('zh-CN', { hour12: false }) }}</td>
              <td v-if="canEdit">
                <div class="actions">
                  <button class="btn small" :disabled="busy" @click="edit(entry)">修改原因</button
                  ><button class="btn small" :disabled="busy" @click="toggle(entry)">
                    {{ entry.active ? '解除拉黑' : '重新拉黑' }}
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="pagination">
        <span>共 {{ data.total }} 位卖家 · 第 {{ page }} 页</span>
        <div class="actions">
          <button class="btn small" :disabled="busy || page <= 1" @click="next(-1)">上一页</button
          ><button
            class="btn small"
            :disabled="busy || page * data.page_size >= data.total"
            @click="next(1)"
          >
            下一页
          </button>
        </div>
      </div>
    </template>
    <div v-else-if="!error" class="empty">
      没有匹配的卖家记录
      <p>可添加卖家并填写原因，或调整搜索条件。</p>
    </div>
  </section>
</template>
