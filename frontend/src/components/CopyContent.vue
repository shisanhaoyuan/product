<script setup lang="ts">
import type { ProductCopy } from '../api/types';
import { plain } from '../utils/display';
defineProps<{ copy: ProductCopy }>();
</script>
<template>
  <div class="copy-title">{{ plain(copy.title) || '未记录标题' }}</div>
  <div class="copy-layout">
    <div>
      <div class="copy-label">KEY FEATURES · 主要功能</div>
      <ol v-if="copy.features.some((value) => plain(value))" class="features">
        <li v-for="(feature, index) in copy.features" :key="index">
          {{ plain(feature) || '此框为空' }}
        </li>
      </ol>
      <p v-else class="muted">未记录卖点</p>
    </div>
    <div>
      <div class="copy-label">DESCRIPTION · 商品描述</div>
      <div class="description">{{ plain(copy.description) || '未记录描述' }}</div>
    </div>
  </div>
</template>
