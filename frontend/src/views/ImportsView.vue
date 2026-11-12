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
const expanded = ref(new Set<number>());
function toggle(id: number) {
  if (expanded.value.has(id)) expanded.value.delete(id);
  else expanded.value.add(id);
}
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
      <p class="subtitle">按上传文件夹汇总，展开查看文件处理明细</p>
    </div>
    <RouterLink class="btn primary" to="/integrations"><AppIcon name="Plus" />接入数据</RouterLink>
  </div>
  <section class="section">
    <div class="section-head"><h2>最近 100 组导入</h2></div>
    <p class="import-history-note">
      仅统计已成功提交的文件，失败文件请在数据接入页重试。旧记录按同来源、同日期汇总，不能区分当天的多次上传。
    </p>
    <div v-if="error" class="error-box">
      {{ error }} <button class="btn small" @click="load">重试</button>
    </div>
    <div v-else-if="loading" class="loading">正在读取…</div>
    <div v-else-if="rows.length" id="import-list" class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>文件夹 / 来源</th>
            <th>已入库文件 / 记录</th>
            <th>新增</th>
            <th>更新</th>
            <th>未变化</th>
            <th>旧记录跳过</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="row in rows" :key="row.id">
            <tr>
              <td>{{ new Date(row.at).toLocaleString('zh-CN', { hour12: false }) }}</td>
              <td>
                <strong>{{ row.label }}</strong
                ><br /><span class="muted"
                  >{{
                    row.kind === 'legacy'
                      ? '旧记录汇总'
                      : row.kind === 'folder'
                        ? '文件夹批次'
                        : '文件批次'
                  }}
                  · {{ row.namespace }}</span
                >
              </td>
              <td>
                <button
                  class="btn small"
                  :aria-expanded="expanded.has(row.id)"
                  @click="toggle(row.id)"
                >
                  {{ row.file_count }} {{ row.kind === 'legacy' ? '条记录' : '个文件' }} ·
                  {{ expanded.has(row.id) ? '收起' : '展开' }}
                </button>
              </td>
              <td>{{ row.result.created }}</td>
              <td>{{ row.result.updated }}</td>
              <td>{{ row.result.unchanged }}</td>
              <td>{{ row.result.older_skipped }}</td>
            </tr>
            <tr v-if="expanded.has(row.id)">
              <td colspan="7" class="import-history-details">
                <table class="data-table" :aria-label="`${row.label} 文件明细`">
                  <thead>
                    <tr>
                      <th>文件路径 / 原来源</th>
                      <th>时间</th>
                      <th>新增</th>
                      <th>更新</th>
                      <th>未变化</th>
                      <th>旧记录跳过</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="file in row.files" :key="file.id">
                      <td>
                        {{ file.path || file.label }}<br /><span class="muted">{{
                          file.namespace
                        }}</span>
                      </td>
                      <td>{{ new Date(file.at).toLocaleString('zh-CN', { hour12: false }) }}</td>
                      <td>{{ file.result.created }}</td>
                      <td>{{ file.result.updated }}</td>
                      <td>{{ file.result.unchanged }}</td>
                      <td>{{ file.result.older_skipped }}</td>
                    </tr>
                  </tbody>
                </table>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
    <div v-else class="empty">还没有导入记录</div>
  </section>
</template>

<style scoped>
.import-history-note {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
  margin: 0 0 18px;
}
.import-history-details {
  background: #f6f8fb;
  padding: 16px 24px;
}
.import-history-details td:first-child {
  overflow-wrap: anywhere;
}
</style>
