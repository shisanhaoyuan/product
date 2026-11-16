<script setup lang="ts">
import { onMounted, ref, watch } from 'vue';
import DOMPurify from 'dompurify';
const props = defineProps<{ modelValue?: unknown; disabled?: boolean; label?: string }>();
const emit = defineEmits<{ 'update:modelValue': [value: string] }>();
const editor = ref<HTMLDivElement>();
const raw = ref(false);
const clean = (value: string) =>
  DOMPurify.sanitize(value, {
    ALLOWED_TAGS: ['p', 'br', 'b', 'strong', 'i', 'em', 'u', 'ul', 'ol', 'li', 'h3', 'h4'],
    ALLOWED_ATTR: [],
  });
function paint() {
  if (editor.value && document.activeElement !== editor.value)
    editor.value.innerHTML = clean(String(props.modelValue ?? ''));
}
function update() {
  if (props.disabled) return;
  emit('update:modelValue', clean(editor.value?.innerHTML || ''));
}
function paste(e: ClipboardEvent) {
  if (props.disabled) return;
  const markup = e.clipboardData?.getData('text/html');
  if (markup) document.execCommand('insertHTML', false, clean(markup));
  else document.execCommand('insertText', false, e.clipboardData?.getData('text/plain') || '');
  update();
}
function format(command: string) {
  editor.value?.focus();
  document.execCommand(command);
  update();
}
watch(() => props.modelValue, paint);
watch(raw, () => requestAnimationFrame(paint));
onMounted(paint);
</script>
<template>
  <div class="listing-richtext">
    <div class="listing-richtext-toolbar">
      <template v-if="!raw">
        <button
          type="button"
          aria-label="加粗"
          :disabled="disabled"
          @mousedown.prevent="format('bold')"
        >
          <strong>B</strong>
        </button>
        <button
          type="button"
          aria-label="斜体"
          :disabled="disabled"
          @mousedown.prevent="format('italic')"
        >
          <em>I</em>
        </button>
        <button
          type="button"
          :disabled="disabled"
          @mousedown.prevent="format('insertUnorderedList')"
        >
          项目列表
        </button>
        <button type="button" :disabled="disabled" @mousedown.prevent="format('removeFormat')">
          清除格式
        </button>
      </template>
      <button type="button" class="listing-source-toggle" @click="raw = !raw">
        {{ raw ? '返回正文' : 'HTML 源码' }}
      </button>
    </div>
    <textarea
      v-if="raw"
      :value="String(modelValue || '')"
      :disabled="disabled"
      :aria-label="label"
      rows="6"
      @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
    />
    <div
      v-else
      ref="editor"
      class="listing-richtext-body"
      :contenteditable="!disabled"
      role="textbox"
      aria-multiline="true"
      :aria-label="label"
      @input="update"
      @blur="update"
      @paste.prevent="paste"
      @drop.prevent
    />
  </div>
</template>
