<script setup lang="ts">
import { computed } from 'vue';
import type { GigaSummary } from '../api/types';
import { money, priceRange, text } from '../utils/display';
import { object, array, level, highRisk, warehouseRange, warehouseEmpty } from '../utils/giga';
import GigaFields from './GigaFields.vue';
const props = defineProps<{ facts: Record<string, unknown>; summary?: GigaSummary | null }>();
const giga = computed(() => object(props.facts.giga));
const detail = computed(() => object(giga.value.detail));
const inventory = computed(() => object(giga.value.inventory));
const sellerStock = computed(() => object(inventory.value.sellerInventoryInfo));
const warehouses = computed(() => array(sellerStock.value.sellerInventoryDistribution));
const seller = computed(() => props.summary?.seller || {});
const metrics = computed(() => [
  {
    name: '商品退货率',
    value: props.summary?.product_return_rate,
    risk: highRisk(props.summary?.product_return_rate),
  },
  {
    name: '卖家退货率',
    value: seller.value.sellerReturnRate,
    risk: highRisk(seller.value.sellerReturnRate),
  },
  { name: '退货批准率', value: seller.value.sellerReturnApprovalRate, risk: false },
  { name: '站内信回复率', value: seller.value.sellerMessageResponseRate, risk: false },
]);
const definitions = [
  {
    title: '基本信息',
    keys: [
      'sku',
      'mpn',
      'productName',
      'category',
      'categoryPath',
      'categoryCode',
      'mainColor',
      'mainMaterial',
      'placeOfOrigin',
      'upc',
      'skuAvailable',
      'customized',
      'whiteLabel',
      'lithiumBatteryContained',
      'comboFlag',
      'partFlag',
      'overSizeFlag',
      'isLGP',
      'isMsf',
      'newArrivalFlag',
      'firstArrivalDate',
    ],
  },
  {
    title: '尺寸与重量',
    keys: [
      'assembledLength',
      'assembledWidth',
      'assembledHeight',
      'assembledWeight',
      'assembledLengthUnit',
      'assembledWeightUnit',
      'length',
      'width',
      'height',
      'lengthUnit',
      'weight',
      'weightUnit',
      'lengthCm',
      'widthCm',
      'heightCm',
      'weightKg',
    ],
  },
  { title: '产品介绍与规格', keys: ['description', 'characteristics', 'attributes', 'customList'] },
  {
    title: '品牌、组合与关联商品',
    keys: ['brandInfo', 'comboInfo', 'associateProductList', 'associateProductInfo'],
  },
  {
    title: '图片、视频与文件',
    keys: [
      'mainImageUrl',
      'imageUrls',
      'fileUrls',
      'videoUrls',
      'productVideoUrl',
      'certificationList',
    ],
  },
  {
    title: '配送、售后与销售限制',
    keys: [
      'afterSalesPolicy',
      'protectionServiceRates',
      'protectionService',
      'remoteAreaPostalCodes',
      'unAvailablePlatform',
    ],
  },
];
const groups = computed(() => {
  const used = new Set(['sellerInfo', 'returnRate']);
  const groups = definitions.map((group) => {
    group.keys.forEach((key) => used.add(key));
    return {
      title: group.title,
      value: Object.fromEntries(
        group.keys.filter((key) => key in detail.value).map((key) => [key, detail.value[key]]),
      ),
    };
  });
  groups.push({
    title: '其他产品信息',
    value: Object.fromEntries(Object.entries(detail.value).filter(([key]) => !used.has(key))),
  });
  return groups.filter((group) => Object.keys(group.value).length);
});
const otherStock = computed(() =>
  Object.fromEntries(
    Object.entries(inventory.value).filter(([key]) => key !== 'sellerInventoryInfo'),
  ),
);
const otherSellerStock = computed(() =>
  Object.fromEntries(
    Object.entries(sellerStock.value).filter(
      ([key]) => key !== 'sellerInventoryDistribution' && key !== 'sellerAvailableInventory',
    ),
  ),
);
const other = computed(() =>
  Object.fromEntries(
    Object.entries(giga.value).filter(([key]) => !['detail', 'price', 'inventory'].includes(key)),
  ),
);
</script>
<template>
  <section class="giga-source" id="section-giga-source">
    <header class="giga-heading">
      <div>
        <span class="giga-eyebrow">GIGA B2B · 来源资料</span>
        <h2>采购价格与供应情况</h2>
      </div>
      <span class="giga-chip">随机仓库</span>
    </header>
    <div class="giga-overview">
      <div class="giga-total">
        <span>预计总额 / 件 · 含打包费</span>
        <strong>{{ priceRange(summary?.estimated_totals || [], summary?.currency || '') }}</strong>
        <p>
          商品单价 {{ money(summary?.unit_price, summary?.currency || '') }}
          <span>＋</span> 物流费（含打包）
          {{ money(summary?.shipping_fee, summary?.currency || '') }}
        </p>
        <small>{{ summary?.price_note || '价格资料未返回' }}。不含指定仓库附加费。</small>
      </div>
      <div class="giga-stat" :class="{ 'giga-danger': summary?.inventory === 0 }">
        <span>Seller 可售库存</span><strong>{{ summary?.inventory ?? '—' }}</strong
        ><small>{{
          summary?.inventory === 0 ? '无可售库存' : '平台返回总量 · 不含 Buyer 自有库存'
        }}</small>
      </div>
      <div
        class="giga-stat"
        :class="{ 'giga-danger': summary?.giga_index != null && Number(summary.giga_index) < 80 }"
      >
        <span>卖家 GIGA Index</span><strong>{{ summary?.giga_index ?? '—' }}</strong
        ><small>低于 80 提醒 · 用户自定规则</small>
      </div>
    </div>
    <div v-if="summary?.risks.length" class="giga-alerts" role="status">
      <span v-for="risk in summary.risks" :key="risk" class="giga-risk">{{ risk }}</span>
    </div>
    <div class="giga-two-col">
      <section class="giga-panel">
        <h3>
          分仓可售库存 <small>{{ warehouses.length }} 个仓库</small>
        </h3>
        <p class="giga-help">数量区间按来源展示，不累加区间上限。</p>
        <div v-if="warehouses.length" class="giga-warehouses">
          <div
            v-for="(warehouse, index) in warehouses"
            :key="index"
            class="giga-warehouse"
            :class="{
              'giga-danger': warehouseEmpty(warehouse.availableQtyMin, warehouse.availableQtyMax),
            }"
          >
            <span>{{ warehouse.warehouseCode || '仓库未命名' }}</span
            ><strong>{{
              warehouseRange(warehouse.availableQtyMin, warehouse.availableQtyMax)
            }}</strong
            ><small v-if="warehouseEmpty(warehouse.availableQtyMin, warehouse.availableQtyMax)"
              >无库存</small
            ><GigaFields
              v-if="
                Object.keys(warehouse).some(
                  (key) => !['warehouseCode', 'availableQtyMin', 'availableQtyMax'].includes(key),
                )
              "
              :value="
                Object.fromEntries(
                  Object.entries(warehouse).filter(
                    ([key]) =>
                      !['warehouseCode', 'availableQtyMin', 'availableQtyMax'].includes(key),
                  ),
                )
              "
            />
          </div>
        </div>
        <p v-else class="giga-muted">接口未返回分仓数量</p>
        <GigaFields :value="otherSellerStock" />
      </section>
      <section class="giga-panel">
        <h3>卖家与表现指标</h3>
        <p class="giga-seller">
          {{ seller.sellerStore || '卖家未返回' }} <small>{{ seller.sellerCode }}</small>
        </p>
        <div class="giga-metrics">
          <div v-for="metric in metrics" :key="metric.name" :class="{ 'giga-danger': metric.risk }">
            <span>{{ metric.name }}</span
            ><strong>{{ level(metric.value) }}</strong>
          </div>
        </div>
        <p class="giga-help">
          GIGA Index 为卖家指标；评分与退货指标随完整商品同步更新。每周巡查仅更新价格和库存。
        </p>
        <details>
          <summary>查看完整卖家指标</summary>
          <GigaFields :value="seller" />
        </details>
      </section>
    </div>
    <section class="giga-panel">
      <h3>Buyer 自有库存及其他库存</h3>
      <p class="giga-help">自有、共享、锁定和在途数量分别展示，不计入 Seller 可售总量。</p>
      <GigaFields :value="otherStock" />
    </section>
    <section v-for="group in groups" :key="group.title" class="giga-panel">
      <h3>{{ group.title }}</h3>
      <GigaFields :value="group.value" />
    </section>
    <section class="giga-panel">
      <h3>完整价格明细</h3>
      <p class="giga-help">
        专享价、折扣价、协议价独立保留；上方预计总额以商品原价计算。物流费已包含打包费，不重复相加。
      </p>
      <GigaFields :value="giga.price" />
    </section>
    <section v-if="Object.keys(other).length" class="giga-panel">
      <h3>其他来源资料</h3>
      <GigaFields :value="other" />
    </section>
    <p class="giga-help">
      以上仅展示接口实际返回的资料，未提供的字段不作推断。商品库编辑值单独保留，不覆盖来源事实。
    </p>
    <details class="details">
      <summary>展开原始 JSON</summary>
      <pre class="json-block">{{ text(giga) }}</pre>
    </details>
  </section>
</template>
