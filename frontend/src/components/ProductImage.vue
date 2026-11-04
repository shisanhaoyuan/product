<script setup lang="ts">
import { ref, watch } from 'vue';
import { safeUrl } from '../utils/display';
import AppIcon from './AppIcon.vue';
const props = withDefaults(
  defineProps<{ src?: string; alt?: string; imageClass?: string; lazy?: boolean }>(),
  { alt: '商品图片', lazy: true },
);
const failed = ref(false);
watch(
  () => props.src,
  () => {
    failed.value = false;
  },
);
</script>
<template>
  <img
    v-if="safeUrl(src) && !failed"
    :src="safeUrl(src)"
    :class="imageClass"
    :alt="alt"
    :loading="lazy ? 'lazy' : 'eager'"
    referrerpolicy="no-referrer"
    @error="failed = true"
  />
  <div v-else class="image-placeholder">
    <span><AppIcon name="ImageOff" /></span>{{ failed ? '图片暂不可用' : '暂无商品图片' }}
  </div>
</template>
