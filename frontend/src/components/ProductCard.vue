<script setup lang="ts">
import GigaQuickFacts from './GigaQuickFacts.vue';
import type { ProductSummary } from '../api/types';
import { priceRange, productStatus } from '../utils/display';
import AppIcon from './AppIcon.vue';
import ProductImage from './ProductImage.vue';
import StatusBadge from './StatusBadge.vue';
import ListingCoverage from './ListingCoverage.vue';
defineProps<{ product: ProductSummary }>();
</script>
<template>
  <RouterLink
    class="product-card"
    :to="(product.source === 'GigaB2B' ? '/giga-product/' : '/product/') + product.id"
    :aria-label="'查看 ' + (product.asin || product.sku)"
  >
    <div class="card-image">
      <StatusBadge :type="productStatus(product).type">{{
        productStatus(product).label
      }}</StatusBadge
      ><ProductImage :src="product.image" :alt="product.title" /><span class="variant-count"
        ><AppIcon name="Layers" />{{
          product.variant_count ? product.variant_count + ' 个子变体' : '未记录子变体'
        }}</span
      ><span class="card-open"><AppIcon name="ArrowUpRight" /></span>
    </div>
    <div class="card-body">
      <div class="brand-row">
        <span class="brand-tag">{{ product.brand || '未填写品牌' }}</span
        ><span class="store-name"><AppIcon name="Store" />{{ product.store }}</span>
      </div>
      <h2 class="product-title" :title="product.title">{{ product.title || '暂无标题' }}</h2>
      <p class="category-label" :title="product.category || '未记录类目'">
        {{ product.category || '未记录类目' }}
      </p>
      <p
        v-if="product.seller_blacklist.length"
        class="seller-risk-label"
        :title="product.seller_blacklist.map((item) => item.seller + '：' + item.reason).join('；')"
      >
        卖家已拉黑 · {{ product.seller_blacklist.length }} 位（含子变体）
      </p>
      <GigaQuickFacts v-if="product.source === 'GigaB2B'" :summary="product.giga_summary" />
      <ListingCoverage v-if="product.source !== 'GigaB2B'" :product="product" />
      <div class="card-meta">
        <template v-if="product.source !== 'GigaB2B'"
          ><span class="meta-label">ASIN</span
          ><span class="mono">{{ product.asin }}</span></template
        ><span class="meta-label">{{ product.source === 'GigaB2B' ? '供应商 SKU' : 'SKU' }}</span
        ><span class="mono" :title="product.sku">{{ product.sku }}</span>
      </div>
      <div class="source-seller">
        <span class="amazon-mark"><AppIcon name="ArrowUpRight" />{{ product.source }}</span
        ><span class="seller" :title="product.seller || '未采集卖家'"
          >卖家 · {{ product.seller || '未采集' }}</span
        >
      </div>
    </div>
    <div class="card-footer">
      <div>
        <span class="price-caption"
          >{{ product.source === 'GigaB2B' ? '预计总额（含打包）' : '售价' }} /
          {{ product.currency ?? 'USD' }}</span
        ><span class="price"
          >{{
            priceRange(
              product.source === 'GigaB2B'
                ? product.giga_summary?.estimated_totals || []
                : product.prices,
              product.currency ?? 'USD',
            )
          }}<small v-if="product.evidence_kind !== 'persisted_readback'">{{
            product.source === 'GigaB2B' ? '供应商资料' : '编辑快照'
          }}</small></span
        >
      </div>
      <span class="detail-link"><AppIcon name="ArrowRight" /></span>
    </div>
  </RouterLink>
</template>
