<script setup lang="ts">
withDefaults(defineProps<{ type?: string }>(), { type: 'neutral' });
</script>
<template>
  <span class="badge" :class="type"><slot /></span>
</template>
