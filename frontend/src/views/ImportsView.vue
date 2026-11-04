<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { catalogApi } from '../api/client';
import type { ImportRecord } from '../api/types';
import { useCatalog } from '../composables/useCatalog';
import { errorMessage } from '../utils/display';
import AppIcon from '../components/AppIcon.vue';
const rows = ref<ImportRecord[]>([]),
  loading = ref(true),
  error = ref('');
const { stats, refreshStats } = useCatalog();
let controller: AbortController | undefined;
async function load() {
  controller?.abort();
  const request = new AbortController();
  controller = request;
  loading.value = true;
  error.value = '';
  try {
    rows.value = await catalogApi.imports(request.signal);
    if (!stats.value) await refreshStats();
  } catch (reason) {
    if (!request.signal.aborted) error.value = errorMessage(reason);
  } finally {
    if (!request.signal.aborted) loading.value = false;
  }
}
onMounted(load);
onBeforeUnmount(() => controller?.abort());
</script>
<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">DATA HISTORY</p>
      <h1>导入记录</h1>
      <p class="subtitle">每次导入独立留痕</p>
    </div>
    <RouterLink class="btn primary" to="/integrations"><AppIcon name="Plus" />接入数据</RouterLink>
  </div>
  <section class="section">
    <div class="section-head"><h2>最近 100 次导入</h2></div>
    <div v-if="error" class="error-box">
      {{ error }} <button class="btn small" @click="load">重试</button>
    </div>
    <div v-else-if="loading" class="loading">正在读取…</div>
    <div v-else-if="rows.length" id="import-list" class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>来源</th>
            <th>新增</th>
            <th>更新</th>
            <th>未变化</th>
            <th>旧记录跳过</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id">
            <td>{{ new Date(row.at).toLocaleString('zh-CN', { hour12: false }) }}</td>
            <td>
              {{ row.label }}<br /><span class="muted mono">{{ row.namespace }}</span>
            </td>
            <td>{{ row.result.created }}</td>
            <td>{{ row.result.updated }}</td>
            <td>{{ row.result.unchanged }}</td>
            <td>{{ row.result.older_skipped }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="empty">还没有导入记录</div>
  </section>
</template>
