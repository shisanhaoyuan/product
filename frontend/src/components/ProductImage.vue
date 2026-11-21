<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { safeUrl } from '../utils/display';
import AppIcon from './AppIcon.vue';
import { imageDisplayCandidates } from '../utils/imageDisplay';
const props = withDefaults(
  defineProps<{
    src?: string;
    fallbacks?: string[];
    alt?: string;
    imageClass?: string;
    lazy?: boolean;
    placeholder?: string;
    listingId?: string;
  }>(),
  { alt: '商品图片', lazy: true, placeholder: '暂无商品图片' },
);
const candidates = computed(() => [
  ...new Set(
    [props.src, ...(props.fallbacks ?? [])]
      .map(safeUrl)
      .filter(Boolean)
      .flatMap((url) => imageDisplayCandidates(url, props.listingId)),
  ),
]);
const index = ref(0);
watch(
  () => JSON.stringify(candidates.value),
  () => {
    index.value = 0;
  },
  { flush: 'sync' },
);
const current = computed(() => candidates.value[index.value]);
function tryNext(event: Event) {
  if ((event.currentTarget as HTMLImageElement).getAttribute('src') === current.value)
    index.value++;
}
</script>
<template>
  <img
    v-if="current"
    :key="current"
    :src="current"
    :class="imageClass"
    :alt="alt"
    :loading="lazy ? 'lazy' : 'eager'"
    referrerpolicy="no-referrer"
    @error="tryNext"
  />
  <div v-else class="image-placeholder">
    <span><AppIcon name="ImageOff" /></span>{{ candidates.length ? '图片暂不可用' : placeholder }}
  </div>
</template>
