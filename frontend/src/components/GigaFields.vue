<script setup lang="ts">
import { computed } from 'vue';
import { label, level, object } from '../utils/giga';
import { plain, safeUrl } from '../utils/display';
const props = withDefaults(defineProps<{ value: unknown; depth?: number }>(), { depth: 0 });
const entries = computed(() => Object.entries(object(props.value)));
const scalar = computed(() =>
  typeof props.value === 'boolean' ? (props.value ? '是' : '否') : level(plain(props.value)),
);
</script>
<template>
  <span v-if="value == null || value === ''" class="giga-muted">未提供</span>
  <span v-else-if="Array.isArray(value) && !value.length" class="giga-muted">无记录</span>
  <ol v-else-if="Array.isArray(value)" class="giga-value-list">
    <li v-for="(entry, index) in value" :key="index">
      <GigaFields :value="entry" :depth="depth + 1" />
    </li>
  </ol>
  <dl
    v-else-if="typeof value === 'object'"
    class="giga-field-grid"
    :class="{ 'giga-nested': depth > 0 }"
  >
    <div
      v-for="[key, entry] in entries"
      :key="key"
      class="giga-field"
      :class="{
        'giga-field-wide':
          (entry !== null && typeof entry === 'object') ||
          ['productName', 'description', 'afterSalesPolicy'].includes(key),
      }"
    >
      <dt>{{ label(key) }}</dt>
      <dd><GigaFields :value="entry" :depth="depth + 1" /></dd>
    </div>
    <span v-if="!entries.length" class="giga-muted">无记录</span>
  </dl>
  <a
    v-else-if="typeof value === 'string' && safeUrl(value)"
    class="detail-link giga-resource"
    :href="safeUrl(value)"
    target="_blank"
    rel="noopener noreferrer"
    >查看资料 ↗<span>{{ value }}</span></a
  >
  <span v-else class="giga-text">{{ scalar }}</span>
</template>
