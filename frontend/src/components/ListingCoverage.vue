<script setup lang="ts">
import type { ProductSummary } from '../api/types';
defineProps<{ product: ProductSummary }>();
</script>

<template>
  <div class="listing-coverage">
    <span class="listing-count">上架 {{ product.listing_count }} 次</span>
    <span class="listing-stores">
      <span v-for="entry in product.listing_stores" :key="entry.store">
        {{ entry.store }}<span class="muted"> · {{ entry.count }} 次</span>
      </span>
    </span>
  </div>
</template>
