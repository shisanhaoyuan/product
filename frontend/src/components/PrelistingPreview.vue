<script setup lang="ts">
import type { PrelistingPreview } from '../api/types';
import { money, plain } from '../utils/display';
import StatusBadge from './StatusBadge.vue';
import ProductImage from './ProductImage.vue';
defineProps<{ preview: PrelistingPreview }>();
</script>
<template>
  <div class="plan-summary">
    <div>
      <span>目标店铺</span><strong>{{ preview.store.name }}</strong>
    </div>
    <div>
      <span>目标品牌</span><strong>{{ preview.brand }}</strong>
    </div>
    <div>
      <span>价格倍率</span><strong>× {{ preview.multiplier }}</strong>
    </div>
    <div>
      <span>商品 / SKU 行</span><strong>{{ preview.total }} / {{ preview.variant_count }}</strong>
    </div>
  </div>
  <p class="notice notice-space">
    预检查通过 {{ preview.ready_count }} 件 · 待复核
    {{ preview.total - preview.ready_count }}
    件。价格基于历史核验记录，执行前需重新采集价格、库存及核对目标文案。
  </p>
  <p v-if="!preview.can_create" class="error-box">
    存在重复 ASIN 或该店铺已有未取消批次，请调整选择。
  </p>
  <details v-for="item in preview.items" :key="item.listing_id" class="plan-item">
    <summary>
      <StatusBadge :type="item.status === 'ready' ? 'good' : 'warn'">{{
        item.status === 'ready' ? '预检查通过' : '待复核'
      }}</StatusBadge
      ><strong>{{ item.sku }}</strong
      ><span>{{ item.title }}</span>
    </summary>
    <div class="plan-item-body">
      <p class="subtitle">
        来源店铺 {{ item.source_store }} · 模板 {{ item.template }} · 历史记录
        {{ item.observed_at }} · 目标 MSRP {{ money(item.msrp) }}
      </p>
      <p v-for="issue in item.errors" :key="issue" class="seller-risk-label">{{ issue }}</p>
      <p v-for="warning in item.warnings" :key="warning" class="subtitle">{{ warning }}</p>
      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th>SKU / ASIN</th>
              <th>目标店铺 SKU</th>
              <th>当前售价</th>
              <th>来源含运费</th>
              <th>首次 ERP 原价</th>
              <th>计价基准</th>
              <th>预上架售价</th>
              <th>历史库存</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="v in item.variants" :key="v.sku">
              <td>
                {{ v.sku }}<br /><span class="muted">{{ v.asin || '缺少 ASIN' }}</span>
              </td>
              <td class="platform-sku">
                <strong v-if="v.platform_sku">{{ v.platform_sku }}</strong>
                <span v-else-if="preview.sku_rule" class="muted"
                  >MY{{ preview.store.sku_code }} · 待分配</span
                >
                <span v-else class="muted">旧批次未分配</span>
              </td>
              <td>{{ money(v.current_price) }}</td>
              <td>{{ money(v.landed) }}</td>
              <td>{{ money(v.original) }}</td>
              <td>{{ money(v.base) }}</td>
              <td>
                <strong>{{ money(v.price) }}</strong>
                <p v-if="v.error" class="seller-risk-label">{{ v.error }}</p>
              </td>
              <td>{{ v.stock ?? '未记录' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <details
        v-for="v in item.variants.filter((v) => v.content)"
        :key="'content-' + v.sku"
        class="details"
      >
        <summary>{{ v.sku }} · 子商品完整内容</summary>
        <template v-if="v.content">
          <p class="subtitle">
            以下为本次批次冻结的商品库内容；目标品牌为
            {{ preview.brand }}，文案品牌仍需在执行前核对。
          </p>
          <div class="variant-image-grid preview-variant-images">
            <ProductImage
              v-for="(url, i) in v.content.images"
              :key="url + i"
              :src="url"
              image-class="variant-thumb"
            />
          </div>
          <p>
            商品编码：{{ v.content.product_id_type || '未记录类型' }} /
            {{ v.content.ean || '未填写' }}
          </p>
          <p>
            色卡属性：{{ v.content.swatch_attribute || '未记录' }} · 运输重量：{{
              v.content.shipping_weight || '未记录'
            }}
            lb
          </p>
          <ProductImage
            v-if="v.content.swatch_image"
            :src="v.content.swatch_image"
            image-class="table-thumb"
          />
          <p>{{ JSON.stringify(v.content.attributes) }}</p>
          <h3>{{ plain(v.content.copy.title) || '未记录标题' }}</h3>
          <ol>
            <li v-for="(feature, i) in v.content.copy.features" :key="i">
              {{ plain(feature) || '空卖点' }}
            </li>
          </ol>
          <div class="description">{{ plain(v.content.copy.description) || '未记录描述' }}</div>
        </template>
      </details>
    </div>
  </details>
</template>
