<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { ProductDetail } from '../api/types';
import { request } from '../api/client';
import { money, errorMessage } from '../utils/display';
import ReadableAttributes from './ReadableAttributes.vue';
import { useAuth } from '../composables/useAuth';
const props = defineProps<{ product: ProductDetail }>();
const emit = defineEmits<{ updated: [] }>();
const { auth } = useAuth();
const canEdit = computed(() => !auth.enabled || auth.user?.role !== 'viewer');
interface Offer {
  Store?: string;
  StockCode?: string;
  LogisticsProductCode?: string;
  LogisticsProductName?: string;
  ReceiveCountryCode?: string;
  CurrencyCode?: string;
  OriginalPrice?: string;
  SellingPrice?: string;
}
interface Stock {
  PlatformStockName?: string;
  PlatformStockCode?: string;
  Qty?: number;
}
interface Warehouse {
  site?: string;
  StockName?: string;
  StockCode?: string;
  Qty?: number;
  StockItemList?: Stock[];
}
interface SaleyeeFacts {
  price?: string;
  shipping?: string | null;
  inventory?: number;
  selected_offer?: Offer;
  warehouses?: Warehouse[];
  warnings?: string[];
  import_warnings?: string[];
  saleyee?: { price?: { WarehousePriceList?: Offer[] }; detail?: Record<string, unknown> };
}
const facts = computed(() => props.product.source_facts as SaleyeeFacts);
const raw = computed(() => facts.value.saleyee || {});
const offers = computed(() => raw.value.price?.WarehousePriceList || []);
const warehouses = computed(() => facts.value.warehouses || []);
const choice = ref('');
const shipping = ref(facts.value.shipping ?? '');
watch(
  () => props.product,
  () => {
    choice.value = '';
    shipping.value = facts.value.shipping ?? '';
  },
);
const attributes = computed(() => props.product.fields['Saleyee attributes'] || {});
const packaging = computed(() => {
  const d = raw.value.detail || {};
  const labels: Record<string, string> = {
    GoodsLength: '商品长度',
    GoodsWidth: '商品宽度',
    GoodsHeight: '商品高度',
    GoodsWeight: '商品重量',
    SpecLength: '包装长度',
    SpecWidth: '包装宽度',
    SpecHeight: '包装高度',
    SpecWeight: '包装重量',
  };
  return Object.fromEntries(
    Object.entries(labels)
      .filter(([key]) => d[key] != null)
      .map(([key, label]) => [label + '（接口未注明单位）', d[key]]),
  );
});
const busy = ref(false),
  error = ref('');
async function refresh() {
  busy.value = true;
  error.value = '';
  const offer =
    choice.value === '' ? facts.value.selected_offer : offers.value[Number(choice.value)];
  try {
    await request(
      `/api/integrations/saleyee/products/${encodeURIComponent(props.product.id)}/refresh`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          warehouse: offer?.StockCode || '',
          logistics: offer?.LogisticsProductCode || '',
          quote_site: offer?.Store || '',
          shipping: shipping.value === '' ? null : shipping.value,
        }),
      },
    );
    emit('updated');
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section class="section">
    <div class="section-head">
      <h2>赛盈价格与分仓库存</h2>
      <span>{{ new Date(product.observed_at).toLocaleString('zh-CN') }}</span>
    </div>
    <div class="section-body saleyee-facts">
      <div class="saleyee-metrics">
        <div>
          <small>计价基准 · USD</small><strong>{{ money(facts.price) }}</strong>
        </div>
        <div>
          <small>对应仓库可用库存</small
          ><strong :class="{ danger: facts.inventory === 0 }">{{
            facts.inventory ?? '待选择报价'
          }}</strong>
        </div>
        <div>
          <small>单独填写运费 · USD</small
          ><strong>{{ facts.shipping != null ? money(facts.shipping) : '待填写' }}</strong>
        </div>
      </div>
      <p v-for="warning in [...(facts.warnings || []), ...(facts.import_warnings || [])]" :key="warning" class="notice">{{ warning }}</p>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>仓库 / 物流</th>
              <th>收货国家</th>
              <th>原价</th>
              <th>当前售价</th>
              <th>币种</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(offer, i) in offers" :key="i">
              <td>
                {{ offer.Store }} / {{ offer.StockCode }} ·
                {{ offer.LogisticsProductName || offer.LogisticsProductCode }}
              </td>
              <td>{{ offer.ReceiveCountryCode || '未返回' }}</td>
              <td>{{ money(offer.OriginalPrice, offer.CurrencyCode) }}</td>
              <td>{{ money(offer.SellingPrice, offer.CurrencyCode) }}</td>
              <td>{{ offer.CurrencyCode }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>站点 / 仓库</th>
              <th>可用库存</th>
              <th>分仓明细</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(region, i) in warehouses" :key="i">
              <td>{{ region.site }}<br />{{ region.StockName || region.StockCode }}</td>
              <td :class="{ danger: region.Qty === 0 }">{{ region.Qty ?? '未知' }}</td>
              <td>
                <div v-for="(stock, j) in region.StockItemList || []" :key="j">
                  {{ stock.PlatformStockName || stock.PlatformStockCode }}：<b
                    :class="{ danger: stock.Qty === 0 }"
                    >{{ stock.Qty ?? '未知' }}</b
                  >
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="canEdit" class="saleyee-selection">
        <label
          >选择美国 USD 报价<select v-model="choice" :disabled="busy" @change="shipping = ''">
            <option value="">沿用当前选择 / 自动匹配唯一报价</option>
            <template v-for="(offer, i) in offers" :key="i"
              ><option
                v-if="offer.CurrencyCode === 'USD' && offer.ReceiveCountryCode === 'US'"
                :value="String(i)"
              >
                {{ offer.Store }} / {{ offer.StockCode }} ·
                {{ offer.LogisticsProductName || offer.LogisticsProductCode }} ·
                {{ money(offer.OriginalPrice || offer.SellingPrice) }}
              </option></template
            >
          </select></label
        ><label
          >此报价的运费（USD）<input
            v-model="shipping"
            type="number"
            min="0"
            max="100000"
            step="0.01"
            :disabled="busy"
            placeholder="确认免运费填 0" /></label
        ><button class="btn primary" :disabled="busy" @click="refresh">
          {{ busy ? '正在同步…' : '刷新价格库存与商品资料' }}
        </button>
      </div>
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
      <h3>商品参数</h3>
      <ReadableAttributes :value="attributes" />
      <h3>包装与尺寸</h3>
      <ReadableAttributes :value="packaging" />
      <details>
        <summary>查看原始 API 信息（包装、属性及分销限制）</summary>
        <pre>{{ JSON.stringify(raw, null, 2) }}</pre>
      </details>
    </div>
  </section>
</template>
<style scoped>
.saleyee-facts {
  display: grid;
  gap: 18px;
}
.saleyee-metrics {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.saleyee-metrics > div {
  display: grid;
  gap: 8px;
  padding: 16px;
  border-radius: 12px;
  background: #eef4f9;
  min-width: 160px;
}
.saleyee-metrics strong {
  font-size: 22px;
}
.saleyee-metrics small {
  color: #637f95;
}
.table-scroll {
  overflow: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}
th,
td {
  text-align: left;
  padding: 12px;
  border-bottom: 1px solid #e0e8ef;
}
th {
  background: #f3f7fa;
}
.danger {
  color: #bf4141;
  font-weight: 700;
}
.saleyee-selection {
  display: flex;
  align-items: end;
  gap: 12px;
  flex-wrap: wrap;
}
label {
  display: grid;
  gap: 8px;
}
select,
input {
  max-width: 100%;
  padding: 10px;
  border: 1px solid #d2dfeb;
  border-radius: 9px;
}
.saleyee-check {
  display: flex;
  align-items: center;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
summary {
  cursor: pointer;
}
</style>
