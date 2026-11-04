<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { prelistingApi } from '../api/prelisting';
import type { BatchPage, PrelistingBatch } from '../api/types';
import { useAuth } from '../composables/useAuth';
import { errorMessage } from '../utils/display';
import PrelistingPreview from '../components/PrelistingPreview.vue';
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
const labels = { draft: '待确认', confirmed: '已确认 · 待接入执行', cancelled: '已取消' };
let controller: AbortController | undefined;
async function load() {
  controller?.abort();
  const current = new AbortController();
  controller = current;
  loading.value = true;
  error.value = '';
  batch.value = undefined;
  message.value = '';
  try {
    if (typeof route.params.id === 'string') {
      const result = await prelistingApi.batch(route.params.id, current.signal);
      if (!current.signal.aborted) batch.value = result;
    } else {
      const result = await prelistingApi.batches(page.value, current.signal);
      if (!current.signal.aborted) data.value = result;
    }
  } catch (e) {
    if (!current.signal.aborted) error.value = errorMessage(e);
  } finally {
    if (!current.signal.aborted) loading.value = false;
  }
}
async function act(action: 'confirm' | 'cancel') {
  if (!batch.value || busy.value) return;
  busy.value = true;
  error.value = '';
  message.value = '';
  try {
    batch.value = await prelistingApi.action(batch.value.id, action);
    message.value =
      action === 'confirm'
        ? '批次已确认。执行接口尚未接入，未进行平台刊登。'
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
onBeforeUnmount(() => controller?.abort());
</script>
<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">LISTING / 上架准备</p>
      <h1>{{ route.params.id ? '批次详情' : '预上架批次' }}</h1>
    </div>
    <div class="actions">
      <RouterLink class="btn" to="/listing-stores">店铺管理</RouterLink
      ><RouterLink class="btn primary" to="/products">选择商品</RouterLink>
    </div>
  </div>
  <p class="notice notice-space">
    当前阶段：生成、核验并确认待上架批次。平台执行接口尚未接入，确认批次不会刊登商品。
  </p>
  <p v-if="message" role="status" class="notice notice-space">{{ message }}</p>
  <div v-if="error" role="alert" class="error-box">
    {{ error }}<button class="btn small" @click="load">刷新</button>
  </div>
  <div v-if="loading" class="loading">正在加载批次…</div>
  <section v-else-if="batch" class="section">
    <div class="section-head">
      <div>
        <h2>{{ labels[batch.status] }}</h2>
        <p>批次 {{ batch.id }} · {{ new Date(batch.created_at).toLocaleString('zh-CN') }}</p>
      </div>
      <RouterLink class="btn small" to="/prelisting">返回批次列表</RouterLink>
    </div>
    <div class="section-body">
      <PrelistingPreview :preview="batch.preview" />
      <div v-if="canEdit && batch.status !== 'cancelled'" class="plan-footer">
        <button class="btn" :disabled="busy" @click="act('cancel')">取消批次</button
        ><button
          v-if="batch.status === 'draft'"
          class="btn primary"
          :disabled="busy || batch.preview.ready_count !== batch.preview.total"
          @click="act('confirm')"
        >
          确认批次（待执行）
        </button>
      </div>
      <p v-if="batch.preview.ready_count !== batch.preview.total" class="subtitle">
        待复核商品也已保存到此批次。补齐来源数据后，取消本批次并重新创建，以更新预览。
      </p>
    </div>
  </section>
  <section v-else-if="data" class="section">
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>创建时间</th>
            <th>目标店铺 / 品牌</th>
            <th>倍率</th>
            <th>商品 / SKU 行</th>
            <th>预检查通过</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in data.items" :key="row.id">
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
      还没有预上架批次
      <p>在商品库筛选或勾选商品，然后点击“预上架”。</p>
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
</template>
