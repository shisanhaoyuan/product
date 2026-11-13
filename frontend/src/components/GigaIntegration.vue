<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { request } from '../api/client';
import { errorMessage } from '../utils/display';
import { useCatalog } from '../composables/useCatalog';
import { useAuth } from '../composables/useAuth';
import StatusBadge from './StatusBadge.vue';
const { auth } = useAuth();
const { refreshStats } = useCatalog('GigaB2B');
const writable = computed(() => !auth.enabled || auth.user?.role !== 'viewer');
const status = ref<{ configured: boolean; environment: string } | null>(null);
const busy = ref(false),
  stopped = ref(false),
  error = ref(''),
  message = ref('');
const page = ref(1),
  totalPages = ref(0);
const counts = ref({ created: 0, updated: 0, unchanged: 0 });
interface PageResult {
  page: number;
  total_pages: number;
  next_page: number | null;
  created: number;
  updated: number;
  unchanged: number;
}
async function load() {
  error.value = '';
  try {
    status.value = await request('/api/integrations/giga');
  } catch (e) {
    error.value = errorMessage(e);
  }
}
async function sync() {
  if (busy.value) return;
  busy.value = true;
  stopped.value = false;
  error.value = '';
  message.value = '';
  page.value = 1;
  totalPages.value = 0;
  counts.value = { created: 0, updated: 0, unchanged: 0 };
  try {
    while (!stopped.value) {
      const result = await request<PageResult>('/api/integrations/giga/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ page: page.value }),
      });
      totalPages.value = result.total_pages;
      void refreshStats().catch(() => {});
      for (const key of ['created', 'updated', 'unchanged'] as const)
        counts.value[key] += result[key];
      if (result.next_page === null) {
        message.value = '同步完成，商品已保存到商品库。';
        break;
      }
      page.value = result.next_page;
    }
    if (stopped.value) message.value = '已停止后续页，已完成页保留。再次同步会自动去重。';
  } catch (e) {
    error.value = `第 ${page.value} 页同步失败：${errorMessage(e)}。已完成页保留，可重新同步。`;
  } finally {
    busy.value = false;
  }
}
onMounted(load);
onBeforeUnmount(() => {
  stopped.value = true;
});
</script>
<template>
  <section class="section">
    <div class="section-head">
      <h2>大建云仓 · GigaB2B</h2>
      <StatusBadge :type="status?.configured ? 'good' : 'warn'">{{
        status?.configured
          ? status.environment === 'production'
            ? '生产环境'
            : '测试环境'
          : '待配置'
      }}</StatusBadge>
    </div>
    <div class="section-body giga-integration">
      <p class="subtitle">同步 Buyer 收藏夹或有囤货库存的商品、图片链接、价格及库存。</p>
      <ul class="steps">
        <li>
          <div>
            <strong>按 SKU 对应，重复同步自动更新</strong
            ><small>保留已编辑内容、来源记录与历史快照。</small>
          </div>
        </li>
        <li>
          <div>
            <strong>原价、运费与可售库存分开保留</strong
            ><small>同步不乘倍率；不会合并关联商品或将配件当成变体。</small>
          </div>
        </li>
      </ul>
      <p v-if="status && !status.configured" class="notice">
        管理员需先在服务器配置 API 环境、Client ID 和 Client Secret。密钥不在网页中填写。
      </p>
      <div class="actions integration-actions">
        <RouterLink class="btn" to="/giga-patrol">价格与库存巡查</RouterLink>
        <RouterLink class="btn" to="/giga-products">打开大建云仓商品库</RouterLink>
        <button
          v-if="writable"
          class="btn primary"
          :disabled="busy || !status?.configured"
          @click="sync"
        >
          {{
            busy ? `正在同步第 ${page} 页${totalPages ? ' / ' + totalPages : ''}…` : '同步商品库'
          }}
        </button>
        <button v-if="busy" class="btn" :disabled="stopped" @click="stopped = true">
          {{ stopped ? '本页完成后停止' : '停止后续页' }}
        </button>
        <button v-else class="btn" @click="load">刷新连接配置</button>
      </div>
      <p v-if="busy || message || error" class="subtitle" role="status">
        新增 {{ counts.created }} · 更新 {{ counts.updated }} · 未变化 {{ counts.unchanged }}
      </p>
      <p v-if="message" class="notice" role="status">{{ message }}</p>
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
      <small class="subtitle"
        >请保持页面打开；离开页面会在本页结束后停止。记录按页保存，不执行上架。</small
      >
    </div>
  </section>
</template>
<style scoped>
.giga-integration {
  display: grid;
  gap: 16px;
}
.giga-integration p {
  margin: 0;
}
</style>
