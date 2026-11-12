<script setup lang="ts">
import { computed, ref } from 'vue';
import AppIcon from './AppIcon.vue';
import StatusBadge from './StatusBadge.vue';
import { useImportQueue } from '../composables/useImportQueue';
import { useCatalog } from '../composables/useCatalog';
import { droppedFiles, selectedFiles } from '../utils/importFiles';
import { errorMessage } from '../utils/display';

const {
  entries,
  reading,
  busy,
  locked,
  error,
  notice,
  pending,
  failed,
  finished,
  result,
  addFiles,
  ingest,
  remove,
  clear,
} = useImportQueue();
const { refreshStats } = useCatalog();
const fileInput = ref<HTMLInputElement>(),
  folderInput = ref<HTMLInputElement>();
const token = ref(''),
  collecting = ref(false),
  dragDepth = ref(0);
const disabled = computed(() => locked.value || collecting.value);
const invalid = computed(() => entries.value.filter((entry) => entry.state === 'invalid').length);
const processed = computed(() => finished.value.length + failed.value.length);
const labels = {
  ready: '待导入',
  invalid: '格式异常',
  importing: '导入中',
  success: '已完成',
  failed: '导入失败',
};

async function select(event: Event) {
  const input = event.target as HTMLInputElement;
  if (input.files) await addFiles(selectedFiles(input.files));
  input.value = '';
}
async function drop(event: DragEvent) {
  dragDepth.value = 0;
  if (disabled.value || !event.dataTransfer) return;
  collecting.value = true;
  error.value = '';
  try {
    await addFiles(await droppedFiles(event.dataTransfer));
  } catch (reason) {
    error.value = `无法读取拖入的文件：${errorMessage(reason)}`;
  } finally {
    collecting.value = false;
  }
}
async function paste(event: ClipboardEvent) {
  if (disabled.value || !event.clipboardData) return;
  const files = selectedFiles(event.clipboardData.files);
  const text = event.clipboardData.getData('text/plain');
  if (!files.length && !text.trim()) return;
  event.preventDefault();
  if (files.length) await addFiles(files);
  else if (/^[\s\uFEFF]*[\[{]/.test(text)) {
    const name = `clipboard-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
    await addFiles([{ file: new File([text], name, { type: 'application/json' }), path: name }]);
  } else {
    error.value =
      '剪贴板中没有可导入的 JSON 文件或 JSON 内容；本地路径不能直接读取，请选择文件夹。';
  }
}
async function submit(retry = false) {
  if (disabled.value) return;
  await ingest(token.value, retry);
  if (finished.value.length) {
    try {
      await refreshStats();
    } catch {
      error.value = '导入结果已保留，但统计刷新失败，请刷新页面查看。';
    }
  }
}
function dragEnter(event: DragEvent) {
  if (!disabled.value && Array.from(event.dataTransfer?.types || []).includes('Files'))
    dragDepth.value++;
}
</script>
<template>
  <form id="import-form" @submit.prevent="submit()">
    <div
      class="import-drop"
      :class="{ 'is-dragging': dragDepth > 0, 'is-disabled': disabled }"
      tabindex="0"
      role="group"
      aria-label="JSON 上传区域，支持拖入文件夹、文件或粘贴 JSON"
      :aria-busy="disabled"
      @dragenter.prevent="dragEnter"
      @dragover.prevent
      @dragleave.prevent="dragDepth = Math.max(0, dragDepth - 1)"
      @drop.prevent.stop="drop"
      @paste="paste"
    >
      <AppIcon name="FolderUp" class="import-drop-symbol" />
      <strong>{{
        collecting ? '正在读取文件夹…' : reading ? '正在解析 JSON…' : 'JSON 文件与文件夹'
      }}</strong>
      <p>拖拽或粘贴至此处 · 每个文件 ≤ 20 MB</p>
      <p>点击区域后按 ⌘V / Ctrl+V，可粘贴文件或 JSON 文本</p>
      <div class="import-picker-actions">
        <button class="btn" type="button" :disabled="disabled" @click="fileInput?.click()">
          <AppIcon name="Files" />选择文件
        </button>
        <button class="btn" type="button" :disabled="disabled" @click="folderInput?.click()">
          <AppIcon name="FolderOpen" />选择文件夹
        </button>
      </div>
      <input
        id="import-file"
        ref="fileInput"
        class="import-native-input"
        type="file"
        accept=".json,application/json"
        multiple
        :disabled="disabled"
        @change="select"
      />
      <input
        id="import-folder"
        ref="folderInput"
        class="import-native-input"
        type="file"
        webkitdirectory
        multiple
        :disabled="disabled"
        @change="select"
      />
    </div>
    <p class="import-help">
      自动读取子文件夹，最多 500 个文件 / 100 MB。选择后先解析，点击批量导入才写入；
      每个文件独立处理，记录按本次上传的顶层文件夹汇总，可展开文件明细；重复数据自动跳过。仅支持商品库格式的
      JSON，原始运行日志请先转换。
    </p>
    <div v-if="notice" class="import-feedback" role="status">{{ notice }}</div>
    <div v-if="entries.length" class="import-queue">
      <div class="import-queue-head">
        <div>
          <strong>{{ entries.length }} 个文件</strong
          ><span
            >待导入 {{ pending.length }} · 异常 {{ invalid + failed.length }} · 完成
            {{ finished.length }}</span
          >
        </div>
        <button
          class="icon-btn"
          type="button"
          title="清空文件队列"
          aria-label="清空文件队列"
          :disabled="disabled"
          @click="clear"
        >
          <AppIcon name="Trash2" />
        </button>
      </div>
      <ul class="import-file-list" aria-label="导入文件队列">
        <li v-for="entry in entries" :key="entry.id" class="import-file-row">
          <AppIcon name="FileJson" />
          <div class="import-file-info">
            <strong :title="entry.path">{{ entry.path }}</strong>
            <small v-if="entry.result"
              >新增 {{ entry.result.created }} · 更新 {{ entry.result.updated }} · 未变化
              {{ entry.result.unchanged }} · 旧记录跳过 {{ entry.result.older_skipped }}</small
            >
            <small v-else-if="entry.message" class="import-file-error">{{ entry.message }}</small>
            <small v-else
              >{{ (entry.bytes / 1024).toFixed(1) }} KB · {{ entry.count }} 个商品模板</small
            >
          </div>
          <StatusBadge
            :type="
              entry.state === 'success'
                ? 'good'
                : ['invalid', 'failed'].includes(entry.state)
                  ? 'bad'
                  : ''
            "
            >{{ labels[entry.state] }}</StatusBadge
          >
          <button
            type="button"
            class="icon-btn"
            :title="`移除 ${entry.path}`"
            :aria-label="`移除 ${entry.path}`"
            :disabled="disabled"
            @click="remove(entry.id)"
          >
            <AppIcon name="X" />
          </button>
        </li>
      </ul>
    </div>
    <div v-if="busy" class="import-progress" role="status">
      <progress :value="processed" :max="entries.length - invalid" aria-label="批量导入进度" />
      <span>已处理 {{ processed }} / {{ entries.length - invalid }}</span>
    </div>
    <label class="form-label" for="import-token">导入令牌（仅在服务端启用时需要）</label>
    <input
      id="import-token"
      v-model="token"
      class="text-input"
      type="password"
      autocomplete="off"
      placeholder="X-Import-Token"
      :disabled="disabled"
    />
    <div class="import-actions">
      <button
        id="submit-import"
        class="btn primary"
        type="submit"
        :disabled="disabled || !pending.length"
      >
        <AppIcon name="Upload" />{{
          busy ? '正在导入…' : `批量导入${pending.length ? ` (${pending.length})` : ''}`
        }}
      </button>
      <button
        v-if="failed.length"
        class="btn"
        type="button"
        :disabled="disabled"
        @click="submit(true)"
      >
        <AppIcon name="RotateCcw" />重试失败文件 ({{ failed.length }})
      </button>
    </div>
    <div id="import-result" class="import-result" aria-live="polite">
      <div v-if="finished.length" class="notice">
        已完成 {{ finished.length }} 个文件：新增 {{ result.created }} · 更新 {{ result.updated }} ·
        未变化 {{ result.unchanged }} · 旧记录跳过 {{ result.older_skipped }}
      </div>
      <div v-if="error" class="error-box">{{ error }}</div>
    </div>
  </form>
</template>
