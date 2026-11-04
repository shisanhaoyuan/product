<script setup lang="ts">
import type { ProductCopy } from '../api/types';
import { plain } from '../utils/display';
import InlineEdit from './InlineEdit.vue';
defineProps<{ copy: ProductCopy; variantSku?: string }>();
</script>
<template>
  <InlineEdit
    :value="copy.title"
    :target="{ field: 'copy.title', variant_sku: variantSku }"
    label="标题"
    ><div class="copy-title">{{ plain(copy.title) || '未记录标题' }}</div></InlineEdit
  >
  <div class="copy-layout">
    <div>
      <div class="copy-label">KEY FEATURES · 主要功能</div>
      <InlineEdit
        :value="copy.features"
        :target="{ field: 'copy.features', variant_sku: variantSku }"
        label="主要功能"
        kind="features"
      >
        <ol v-if="copy.features.some((value) => plain(value))" class="features">
          <li v-for="(feature, index) in copy.features" :key="index">
            {{ plain(feature) || '此框为空' }}
          </li>
        </ol>
        <p v-else class="muted">未记录卖点</p>
      </InlineEdit>
    </div>
    <div>
      <div class="copy-label">DESCRIPTION · 商品描述</div>
      <InlineEdit
        :value="copy.description"
        :target="{ field: 'copy.description', variant_sku: variantSku }"
        label="商品描述"
        kind="multiline"
        ><div class="description">{{ plain(copy.description) || '未记录描述' }}</div></InlineEdit
      >
    </div>
  </div>
</template>
