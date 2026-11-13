<script setup lang="ts">
import type { GigaSummary } from '../api/types';
defineProps<{ summary?: GigaSummary | null }>();
</script>
<template>
  <div class="giga-quick" v-if="summary">
    <span :class="{ 'giga-danger': summary.inventory === 0 }"
      >可售库存 <b>{{ summary.inventory ?? '—' }}</b></span
    >
    <span :class="{ 'giga-danger': summary.giga_index != null && Number(summary.giga_index) < 80 }"
      >GIGA Index <b>{{ summary.giga_index ?? '—' }}</b></span
    >
    <div v-if="summary.risks.length" class="giga-alerts">
      <span v-for="risk in summary.risks" :key="risk" class="giga-risk">{{ risk }}</span>
    </div>
  </div>
</template>
