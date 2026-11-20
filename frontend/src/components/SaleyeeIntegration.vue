<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, computed } from 'vue';
import { request } from '../api/client';
import { errorMessage } from '../utils/display';
import { useAuth } from '../composables/useAuth';
import { useCatalog } from '../composables/useCatalog';
const { auth } = useAuth();
const { refreshStats } = useCatalog('Saleyee');
const writable = computed(() => !auth.enabled || auth.user?.role !== 'viewer');
const config = ref<{ configured: boolean; account: string }>();
const skus = ref(''),
  warehouse = ref(''),
  logistics = ref('');
const busy = ref(false),
  stopped = ref(false);
const message = ref(''),
  error = ref(''),
  page = ref(1);
const counts = ref({ created: 0, updated: 0, unchanged: 0 });
interface SyncResult {
  next_page: number | null;
  total_pages: number;
  created: number;
  updated: number;
  unchanged: number;
}
async function load() {
  try {
    config.value = await request('/api/integrations/saleyee');
  } catch (e) {
    error.value = errorMessage(e);
  }
}
async function sync() {
  const selected = [...new Set(skus.value.split(/[\s,，;；]+/).filter(Boolean))];
  if (selected.length > 30) {
    error.value = '按 SKU 同步时每次最多 30 个；留空可分页同步授权商品。';
    return;
  }
  busy.value = true;
  stopped.value = false;
  error.value = '';
  message.value = '';
  page.value = 1;
  counts.value = { created: 0, updated: 0, unchanged: 0 };
  // Freeze filters for all pages so editing an input cannot mix scopes during a run.
  const scope = {
    skus: selected,
    warehouse: warehouse.value.trim(),
    logistics: logistics.value.trim(),
    end_date: new Date().toISOString().slice(0, 10),
  };
  try {
    while (!stopped.value) {
      const result = await request<SyncResult>('/api/integrations/saleyee/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...scope, page: page.value }),
      });
      for (const key of ['created', 'updated', 'unchanged'] as const)
        counts.value[key] += result[key];
      void refreshStats().catch(() => {});
      if (result.next_page === null) {
        message.value = '同步完成，已保存到赛盈商品库。';
        break;
      }
      page.value = result.next_page;
    }
    if (stopped.value) message.value = '已停止后续页，已完成页保留；再次同步自动去重。';
  } catch (e) {
    error.value = `第 ${page.value} 页：${errorMessage(e)}；已完成页保留。`;
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
      <h2>赛盈 · Saleyee</h2>
      <span>{{ config?.configured ? '已填写 V2 凭证' : '待配置' }}</span>
    </div>
    <div class="section-body saleyee-integration">
      <p>
        同步商品详情、图片、仓库报价与可用库存。凭证已填写不代表接口验证通过。同账号、同 SKU
        重复同步自动更新，保留编辑记录。
      </p>
      <p v-if="!config?.configured" class="notice">
        管理员需配置服务器的 CATALOG_SALEYEE_ACCESS_TOKEN 和固定账号标识
        CATALOG_SALEYEE_ACCOUNT。令牌不在网页中显示。
      </p>
      <label
        >供应商 SKU（可选，最多 30 个）<textarea
          v-model="skus"
          :disabled="busy"
          rows="3"
          placeholder="用空格、逗号或换行分隔；留空分页同步账号授权范围的商品"
        />
      </label>
      <div class="saleyee-scope">
        <label
          >仓库编码（可选）<input
            v-model="warehouse"
            :disabled="busy"
            placeholder="留空保留全部仓库报价"
        /></label>
        <label
          >物流编码（可选）<input
            v-model="logistics"
            :disabled="busy"
            placeholder="留空；多报价时在商品档案选择"
        /></label>
      </div>
      <small
        >赛盈采购无需另加运费，预上架按采购价 ×
        倍率计算。多条报价时，请在商品档案选择对应仓库与物流。</small
      >
      <div class="actions">
        <RouterLink class="btn" to="/saleyee-patrol">价格与库存巡查</RouterLink>
        <RouterLink class="btn" to="/saleyee-products">打开赛盈商品库</RouterLink
        ><button
          v-if="writable"
          class="btn primary"
          :disabled="busy || !config?.configured"
          @click="sync"
        >
          {{ busy ? `正在同步第 ${page} 页…` : '同步商品' }}</button
        ><button v-if="busy" class="btn" @click="stopped = true">停止后续页</button
        ><button v-else class="btn" @click="load">刷新配置</button>
      </div>
      <p v-if="busy || message">
        新增 {{ counts.created }} · 更新 {{ counts.updated }} · 未变化 {{ counts.unchanged }}
      </p>
      <p v-if="message" class="notice" role="status">{{ message }}</p>
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
    </div>
  </section>
</template>
<style scoped>
.saleyee-integration {
  display: grid;
  gap: 16px;
}
p {
  margin: 0;
  line-height: 1.7;
}
label {
  display: grid;
  gap: 8px;
  color: #516b81;
  font-size: 14px;
}
textarea,
input:not([type='checkbox']) {
  width: 100%;
  min-width: 0;
  border: 1px solid #d3dfeb;
  border-radius: 9px;
  padding: 10px 12px;
  background: #fff;
  color: #334d65;
  font: inherit;
}
.saleyee-scope {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.saleyee-confirm {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  line-height: 1.6;
}
small {
  color: #73869a;
}
@media (max-width: 600px) {
  .saleyee-scope {
    grid-template-columns: 1fr;
  }
}
</style>
