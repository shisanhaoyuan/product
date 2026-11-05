<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue';
import { useProductEditing, type EditTarget } from '../composables/useProductEditing';
import { errorMessage, text } from '../utils/display';
import AppIcon from './AppIcon.vue';
const props = withDefaults(
  defineProps<{
    value: unknown;
    target: EditTarget;
    label: string;
    kind?: 'text' | 'multiline' | 'features' | 'images' | 'images-url' | 'money' | 'stock' | 'json';
    readonly?: boolean;
  }>(),
  { kind: 'text', readonly: false },
);
const context = useProductEditing();
const enabled = computed(() => !!context?.enabled.value && !props.readonly);
const instanceId = useId();
const identity = computed(() => instanceId + JSON.stringify(props.target));
const editing = computed(() => context?.active.value === identity.value);
const input = ref<HTMLElement>();
const textSurface = ref<HTMLElement>();
let textElement: HTMLElement | undefined;
const inPlace = computed(() => props.kind === 'text' || props.kind === 'multiline');
const textChanged = ref(false);
const draft = ref(''),
  features = ref<string[]>([]),
  error = ref('');
async function begin() {
  if (!enabled.value || !context || context.busy.value || context.active.value) return;
  error.value = '';
  textChanged.value = false;
  draft.value =
    props.kind === 'images'
      ? (props.value as string[]).join('\n')
      : props.kind === 'json'
        ? JSON.stringify(props.value, null, 2)
        : text(props.value);
  features.value = Array.isArray(props.value) ? [...props.value] : [];
  context.active.value = identity.value;
  await nextTick();
  if (inPlace.value && textSurface.value) {
    textElement = (textSurface.value.firstElementChild || textSurface.value) as HTMLElement;
    if (!text(props.value)) {
      textElement.textContent = '';
    }
    textElement.setAttribute('contenteditable', 'plaintext-only');
    textElement.setAttribute('role', 'textbox');
    textElement.setAttribute('aria-label', '编辑' + props.label);
    textElement.setAttribute('aria-multiline', String(props.kind === 'multiline'));
    textElement.focus();
  } else input.value?.querySelector<HTMLElement>('input,textarea,select')?.focus();
}
function updateText() {
  draft.value = textElement?.innerText || '';
  textChanged.value = true;
}
function textKeydown(event: KeyboardEvent) {
  if (event.isComposing) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    cancel();
  } else if (event.key === 'Enter' && (props.kind === 'text' || event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    void save();
  }
}
function cancel() {
  if (context && !context.busy.value) {
    context.active.value = '';
    error.value = '';
  }
}
async function save() {
  if (!context || !editing.value || context.busy.value) return;
  error.value = '';
  try {
    let value: unknown = draft.value;
    if (inPlace.value && !textChanged.value) value = props.value;
    if (props.kind === 'money') value = String(draft.value);
    if (props.kind === 'features') value = [...features.value];
    if (props.kind === 'images')
      value = draft.value
        .split('\n')
        .map((v) => v.trim())
        .filter(Boolean);
    if (props.kind === 'json') value = JSON.parse(draft.value);
    await context.save(props.target, value);
    context.active.value = '';
  } catch (e) {
    error.value = errorMessage(e);
  }
}
function saveOnOutsideClick(event: MouseEvent) {
  const target = event.target;
  if (event.button !== 0 || !(target instanceof Element) || input.value?.contains(target)) return;
  // Commands and navigation keep their own behavior; blank areas commit the draft.
  if (target.closest('a,button,input,textarea,select,summary,[role="button"],[tabindex]')) return;
  void save();
}
watch(editing, (active, _, onCleanup) => {
  if (!active) return;
  document.addEventListener('click', saveOnOutsideClick, true);
  onCleanup(() => document.removeEventListener('click', saveOnOutsideClick, true));
});
watch(
  () => context?.busy.value,
  (busy) => {
    if (editing.value && textElement) {
      textElement.setAttribute('contenteditable', busy ? 'false' : 'plaintext-only');
      textElement.setAttribute('aria-busy', String(!!busy));
    }
  },
);
onBeforeUnmount(() => {
  if (context?.active.value === identity.value) context.active.value = '';
});
</script>
<template>
  <div
    v-if="editing"
    ref="input"
    class="inline-editor"
    :class="{ 'inline-editor-in-place': inPlace }"
    @click.stop
    @dblclick.stop
    @keydown.stop
    @keydown.esc="!inPlace && cancel()"
    @keydown.ctrl.enter="!inPlace && save()"
    @keydown.meta.enter="!inPlace && save()"
  >
    <div
      v-if="inPlace"
      ref="textSurface"
      class="editable-value in-place-text"
      @input="updateText"
      @keydown="textKeydown"
    >
      <slot
        ><span>{{ text(value) }}</span></slot
      >
    </div>
    <div v-else-if="kind === 'features'" class="feature-editor">
      <div v-for="(_, i) in features" :key="i" class="feature-editor-row">
        <span>{{ i + 1 }}</span
        ><textarea
          v-model="features[i]"
          :aria-label="label + '第' + (i + 1) + '条'"
          rows="3"
          :disabled="context?.busy.value"
        />
        <button
          type="button"
          class="inline-icon"
          :title="'删除第 ' + (i + 1) + ' 条'"
          :aria-label="'删除第 ' + (i + 1) + ' 条'"
          :disabled="context?.busy.value"
          @click="features.splice(i, 1)"
        >
          <AppIcon name="X" />
        </button>
      </div>
      <button
        type="button"
        class="btn small"
        :disabled="context?.busy.value || features.length >= 30"
        @click="features.push('')"
      >
        <AppIcon name="Plus" />添加卖点
      </button>
    </div>
    <textarea
      v-else-if="['multiline', 'json', 'images'].includes(kind)"
      v-model="draft"
      :aria-label="'编辑' + label"
      :disabled="context?.busy.value"
      rows="6"
    />
    <select
      v-else-if="kind === 'stock'"
      v-model="draft"
      :aria-label="'编辑' + label"
      :disabled="context?.busy.value"
    >
      <option value="0">0</option>
      <option value="10">10</option>
    </select>
    <input
      v-else
      v-model="draft"
      :type="kind === 'money' ? 'number' : 'text'"
      :step="kind === 'money' ? '0.01' : undefined"
      :min="kind === 'money' ? '0.01' : undefined"
      :aria-label="'编辑' + label"
      :disabled="context?.busy.value"
      @keydown.enter.prevent="save"
    />
    <div v-if="error || context?.busy.value" class="inline-editor-actions">
      <span v-if="error" class="inline-error" role="alert">{{ error }}</span>
      <span v-if="context?.busy.value" role="status">正在保存…</span>
    </div>
  </div>
  <div
    v-else
    :class="{ 'editable-value': enabled }"
    :role="enabled ? 'button' : undefined"
    :tabindex="enabled ? 0 : undefined"
    :title="enabled ? '双击编辑' + label : undefined"
    :aria-label="enabled ? '编辑' + label : undefined"
    @dblclick.stop="begin"
    @keydown.enter.stop.prevent="begin"
  >
    <slot
      ><span>{{ text(value) || '未填写' }}</span></slot
    >
  </div>
</template>
