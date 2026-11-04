<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue';
import { useProductEditing } from '../composables/useProductEditing';
import { errorMessage, safeUrl } from '../utils/display';
import ProductImage from './ProductImage.vue';
import AppIcon from './AppIcon.vue';
import { catalogApi } from '../api/client';

const props = defineProps<{ images: string[]; sku?: string; asin?: string; hero?: boolean }>();
const scope = computed(() => (props.sku === undefined ? '主商品' : '子商品'));
const editing = useProductEditing();
const identity = 'images-' + useId();
const dialog = ref<HTMLDialogElement>();
const opened = ref(false),
  selected = ref(0),
  links = ref(''),
  error = ref('');
const editable = computed(() => !!editing?.enabled.value);
const working = ref(false),
  uploadInput = ref<HTMLInputElement>(),
  candidates = ref<string[]>([]),
  chosen = ref<string[]>([]),
  notice = ref('');
const busy = computed(() => !!editing?.busy.value || working.value);
async function upload(event: Event) {
  const files = Array.from((event.target as HTMLInputElement).files || []);
  if (!editing || !files.length || busy.value) return;
  error.value = '';
  notice.value = '';
  if (
    files.length + props.images.length > 100 ||
    files.some((file) => file.size > 8 * 1024 * 1024)
  ) {
    error.value = '每张最多 8 MB，每组最多 100 张图片。';
    if (uploadInput.value) uploadInput.value.value = '';
    return;
  }
  working.value = true;
  const urls: string[] = [];
  try {
    for (const file of files) {
      const result = await catalogApi.uploadImage(editing.listingId.value, props.sku, file);
      urls.push(result.url);
    }
    await editing.save({ field: 'images', variant_sku: props.sku }, [
      ...new Set([...props.images, ...urls]),
    ]);
    selected.value = props.images.length - 1;
    notice.value = `已导入 ${files.length} 张本地图片`;
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    working.value = false;
    if (uploadInput.value) uploadInput.value.value = '';
  }
}
async function capture() {
  if (!editing || busy.value) return;
  working.value = true;
  error.value = '';
  notice.value = '';
  candidates.value = [];
  chosen.value = [];
  try {
    const result = await catalogApi.captureImages(editing.listingId.value, props.sku);
    candidates.value = result.images;
    notice.value = `已核验 ASIN ${result.asin}，抓取到 ${result.images.length} 张图片；请选择需要添加的图片。`;
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    working.value = false;
  }
}
async function addCaptured() {
  const images = [...new Set([...props.images, ...chosen.value])];
  if (images.length > 100) {
    error.value = '每组最多 100 张图片';
    return;
  }
  if (await persist(images)) {
    chosen.value = [];
    notice.value = '所选来源图片已添加';
  }
}
const blocked = computed(
  () => busy.value || (!!editing?.active.value && editing.active.value !== identity),
);
watch(
  () => props.images.length,
  (length) => {
    selected.value = Math.max(0, Math.min(selected.value, length - 1));
  },
);
async function open(index = 0) {
  if (blocked.value) return;
  selected.value = index;
  error.value = '';
  links.value = '';
  candidates.value = [];
  chosen.value = [];
  notice.value = '';
  opened.value = true;
  if (editing && editable.value) editing.active.value = identity;
  await nextTick();
  dialog.value?.showModal();
}
function close() {
  if (busy.value) return;
  if (
    (links.value.trim() || chosen.value.length) &&
    !window.confirm('还有图片尚未添加，关闭将放弃本次选择。继续关闭？')
  )
    return;
  dialog.value?.close();
  opened.value = false;
  if (editing?.active.value === identity) editing.active.value = '';
}
async function persist(images: string[]) {
  if (!editing || !editable.value || busy.value) return false;
  error.value = '';
  try {
    await editing.save({ field: 'images', variant_sku: props.sku }, images);
    return true;
  } catch (e) {
    error.value = errorMessage(e);
    return false;
  }
}
async function remove(index: number) {
  await persist(props.images.filter((_, i) => i !== index));
}
async function add() {
  const urls = links.value
    .split(/\r?\n/)
    .map((v) => v.trim())
    .filter(Boolean);
  if (!urls.length || urls.some((url) => !safeUrl(url))) {
    error.value = '请输入有效的 HTTP/HTTPS 图片链接，每行一条。';
    return;
  }
  const images = [...new Set([...props.images, ...urls])];
  if (images.length > 100) {
    error.value = '每组最多保存 100 张图片。';
    return;
  }
  if (await persist(images)) {
    links.value = '';
    selected.value = images.length - 1;
  }
}
function step(offset: number) {
  if (props.images.length)
    selected.value = (selected.value + offset + props.images.length) % props.images.length;
}
onBeforeUnmount(() => {
  if (editing?.active.value === identity) editing.active.value = '';
});
</script>
<template>
  <div class="variant-images" @click.stop @dblclick.stop @keydown.stop>
    <button
      v-if="hero"
      type="button"
      class="gallery-main gallery-preview-button"
      :aria-label="'放大' + scope + '图片'"
      :disabled="blocked || !images.length"
      @click="open(selected)"
    >
      <ProductImage :src="images[selected]" :alt="scope + '主图'" :lazy="false" />
    </button>
    <div :class="hero ? 'gallery-thumbs' : 'variant-image-grid'">
      <button
        v-for="(url, index) in images"
        :key="url + index"
        type="button"
        :class="hero ? { selected: selected === index } : 'variant-image-button'"
        :aria-label="(sku ?? scope) + ' 放大第 ' + (index + 1) + ' 张图片'"
        :disabled="blocked"
        @click="open(index)"
      >
        <ProductImage
          :src="url"
          :alt="'第 ' + (index + 1) + ' 张图片'"
          image-class="variant-thumb"
        />
      </button>
    </div>
    <div class="variant-image-caption">
      <span>{{ images.length }} 张图片 · 点击放大</span
      ><button v-if="editable" type="button" class="btn small" :disabled="blocked" @click="open()">
        <AppIcon name="Plus" />管理图片
      </button>
    </div>
    <Teleport to="body">
      <dialog
        v-if="opened"
        ref="dialog"
        class="image-manager-dialog"
        aria-labelledby="image-manager-title"
        @cancel.prevent="close"
        @click.stop
        @keydown.stop
      >
        <header class="composer-header">
          <div>
            <h2 id="image-manager-title">{{ scope }}图片</h2>
            <p>{{ sku ?? asin ?? scope }} · {{ images.length }} 张图片</p>
          </div>
          <button class="composer-close" aria-label="关闭图片查看" :disabled="busy" @click="close">
            <AppIcon name="X" />
          </button>
        </header>
        <div class="image-manager-body">
          <div class="image-manager-preview">
            <button
              v-if="images.length > 1"
              class="image-step"
              aria-label="上一张图片"
              @click="step(-1)"
            >
              <AppIcon name="ChevronLeft" />
            </button>
            <div class="image-manager-stage">
              <ProductImage
                v-if="images.length"
                :key="images[selected]"
                :src="images[selected]"
                alt="放大图片"
                image-class="image-enlarged"
                :lazy="false"
              />
              <p v-else class="muted">暂无图片，可在下方添加。</p>
            </div>
            <button
              v-if="images.length > 1"
              class="image-step"
              aria-label="下一张图片"
              @click="step(1)"
            >
              <AppIcon name="ChevronRight" />
            </button>
          </div>
          <div class="image-manager-current" v-if="images.length">
            <span>{{ selected + 1 }} / {{ images.length }}</span
            ><a
              v-if="safeUrl(images[selected])"
              :href="safeUrl(images[selected])"
              target="_blank"
              rel="noopener noreferrer"
              >打开原图 ↗</a
            ><button v-if="editable" class="btn small" :disabled="busy" @click="remove(selected)">
              删除当前图片
            </button>
          </div>
          <div class="image-manager-thumbs">
            <button
              v-for="(url, index) in images"
              :key="url + index"
              :class="{ selected: selected === index }"
              :aria-label="'预览第 ' + (index + 1) + ' 张图片'"
              @click="selected = index"
            >
              <ProductImage :src="url" image-class="variant-thumb" />
            </button>
          </div>
          <form v-if="editable" class="image-add-form" @submit.prevent="add">
            <div class="image-import-actions">
              <input
                ref="uploadInput"
                class="image-file-input"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                multiple
                aria-label="选择本地图片"
                :disabled="busy"
                @change="upload"
              />
              <button
                type="button"
                class="btn small"
                :disabled="busy"
                @click="uploadInput?.click()"
              >
                <AppIcon name="Upload" />本地导入图片
              </button>
              <button type="button" class="btn small" :disabled="busy || !asin" @click="capture">
                <AppIcon name="RotateCcw" />按 ASIN 重新抓取
              </button>
              <span class="muted"
                >{{ asin || '缺少 ASIN' }} · JPG / PNG / WebP · 每张最多 8 MB</span
              >
            </div>
            <label for="image-add-links">添加图片链接（每行一条）</label
            ><textarea
              id="image-add-links"
              v-model="links"
              rows="3"
              placeholder="https://…"
              :disabled="busy"
            />
            <div>
              <span class="muted">添加、删除后立即保存到此{{ scope }}；未同步 ERP。</span
              ><button class="btn primary small" :disabled="busy || !links.trim()">
                <AppIcon name="Plus" />添加图片
              </button>
            </div>
          </form>
          <p v-if="notice" role="status" class="muted">{{ notice }}</p>
          <div v-if="candidates.length" class="image-capture-results">
            <div class="image-capture-grid">
              <label v-for="(url, i) in candidates" :key="url"
                ><input
                  v-model="chosen"
                  type="checkbox"
                  :value="url"
                  :aria-label="'选择抓取图片 ' + (i + 1)"
                  :disabled="busy" /><ProductImage :src="url" image-class="variant-thumb"
              /></label>
            </div>
            <button
              class="btn primary small"
              :disabled="busy || !chosen.length"
              @click="addCaptured"
            >
              添加所选 {{ chosen.length }} 张图片
            </button>
          </div>
          <p v-if="error" role="alert" class="inline-error">{{ error }}</p>
          <p v-if="busy" role="status" class="muted">正在处理图片，请稍候…</p>
        </div>
      </dialog>
    </Teleport>
  </div>
</template>
