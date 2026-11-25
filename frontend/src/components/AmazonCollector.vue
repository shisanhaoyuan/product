<script setup lang="ts">
import CollectorPluginLinks from './CollectorPluginLinks.vue';
import { collectorPluginVersion } from '../utils/collectorPlugin';
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';
import {
  collectorApi,
  type CollectorJob,
  type CollectorTask,
  type SheetPreview,
} from '../api/amazonCollector';
import { collectTask } from '../utils/amazonCollector';
import { useAuth } from '../composables/useAuth';
import { useCatalog } from '../composables/useCatalog';
import { errorMessage } from '../utils/display';
const { auth } = useAuth();
const writable = computed(() => !auth.enabled || auth.user?.role !== 'viewer');
const text = ref(''),
  includeVariants = ref(true),
  jobs = ref<CollectorJob[]>([]),
  selected = ref<CollectorJob>();
const busy = ref(false),
  uploading = ref(false),
  dragging = ref(false),
  error = ref(''),
  notice = ref('');
const sheet = ref<SheetPreview>(),
  fileInput = ref<HTMLInputElement>(),
  wraith = ref(false);
const openPage = ref<() => void>();
const cancelling = ref(false);
let cancelRequested = false;
let controller: AbortController | undefined;
let createId = crypto.randomUUID();
function resetCreateId() {
  createId = crypto.randomUUID();
}
const route = useRoute();
const { refreshStats } = useCatalog('Amazon');
const completed = computed(
  () => selected.value?.items.filter((i) => i.state === 'done').length || 0,
);
const labels: Record<string, string> = {
  pending: '等待采集',
  running: '浏览器采集',
  fallback_queued: 'Wraith 排队',
  fallback_running: 'Wraith 处理中',
  done: '已采集',
  failed: '采集失败',
  collecting: '待继续／采集中',
  paused: '已暂停',
  complete: '已结束',
  cancelled: '已取消',
};
function update(job: CollectorJob) {
  if (
    jobs.value.some((existing) => existing.id === job.id && existing.state === 'cancelled') &&
    job.state !== 'cancelled'
  )
    return;
  selected.value = job;
  jobs.value = [job, ...jobs.value.filter((j) => j.id !== job.id)].slice(0, 20);
}
async function load() {
  try {
    const data = await collectorApi.status();
    jobs.value = data.jobs;
    wraith.value = data.wraith_configured;
  } catch (e) {
    error.value = errorMessage(e);
  }
}
async function readFile(file: File) {
  if (busy.value || uploading.value) return;
  error.value = '';
  uploading.value = true;
  try {
    if (file.size > 150 * 1024 * 1024) throw new Error('表格最大 150 MB');
    sheet.value = await collectorApi.sheet(file);
    text.value = sheet.value.asins.join('\n');
    createId = crypto.randomUUID();
    notice.value = '表格已识别，请核对数量后点击开始采集。';
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    uploading.value = false;
  }
}
async function drop(e: DragEvent) {
  dragging.value = false;
  const files = e.dataTransfer?.files;
  if (files?.length !== 1) {
    error.value = '请每次拖入一份 Excel 或 CSV 表格';
    return;
  }
  await readFile(files[0]!);
}
async function selectFile(e: Event) {
  const input = e.target as HTMLInputElement;
  if (input.files?.[0]) await readFile(input.files[0]);
  input.value = '';
}
async function run(job?: CollectorJob) {
  if (busy.value || cancelling.value) return;
  busy.value = true;
  cancelRequested = false;
  if (!job) selected.value = undefined;
  error.value = '';
  notice.value = '';
  openPage.value = undefined;
  controller = new AbortController();
  try {
    update(job || (await collectorApi.create(text.value, includeVariants.value, createId)));
    if (!job) createId = crypto.randomUUID();
    const id = selected.value!.id;
    while (!controller.signal.aborted) {
      const next = await collectorApi.action<{ task: CollectorTask | null; job: CollectorJob }>(
        id,
        'claim',
      );
      if (controller.signal.aborted) break;
      update(next.job);
      if (['cancelled', 'complete'].includes(next.job.state)) break;
      if (!next.task) {
        update(await collectorApi.action(id, 'finish'));
        notice.value = '采集任务已结束，成功取得的商品已入库；缺项和失败项保留在下方。';
        void refreshStats().catch(() => {});
        break;
      }
      notice.value = `正在后台采集 ${next.task.asin}`;
      openPage.value = undefined;
      await collectTask(
        id,
        next.task,
        controller.signal,
        wraith.value,
        update,
        (message, reveal) => {
          notice.value = message;
          openPage.value = reveal;
        },
      );
    }
  } catch (e) {
    error.value = controller.signal.aborted ? '' : errorMessage(e);
    if (
      !controller.signal.aborted &&
      !cancelRequested &&
      selected.value &&
      !['complete', 'cancelled'].includes(selected.value.state)
    ) {
      try {
        update(await collectorApi.action(selected.value.id, 'pause'));
      } catch {
        notice.value = '请刷新任务查看实际状态；已采集结果保留。';
      }
    }
  } finally {
    if (
      controller.signal.aborted &&
      !cancelRequested &&
      selected.value &&
      !['complete', 'cancelled'].includes(selected.value.state)
    ) {
      try {
        update(await collectorApi.action(selected.value.id, 'pause'));
      } catch {
        notice.value = '请刷新任务查看实际状态；已采集结果保留。';
      }
    }
    busy.value = false;
    openPage.value = undefined;
  }
}
async function pause() {
  controller?.abort();
}
async function cancel(job: CollectorJob) {
  if (cancelling.value || (busy.value && selected.value?.id !== job.id)) return;
  const active = busy.value && selected.value?.id === job.id;
  cancelling.value = true;
  error.value = '';
  if (active) {
    cancelRequested = true;
    controller?.abort();
  }
  try {
    update(await collectorApi.action(job.id, 'cancel'));
    notice.value = '已取消采集，本批结果未入库。';
  } catch (e) {
    error.value = errorMessage(e);
    notice.value = '请刷新任务查看实际状态，核对是否取消成功。';
  } finally {
    cancelling.value = false;
    openPage.value = undefined;
  }
}
async function finish(job: CollectorJob) {
  if (busy.value || cancelling.value) return;
  busy.value = true;
  error.value = '';
  try {
    update(await collectorApi.action(job.id, 'finish'));
    void refreshStats().catch(() => {});
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
onMounted(() => {
  if (typeof route.query.amazon === 'string') text.value = route.query.amazon;
  void load();
});
onBeforeUnmount(() => {
  controller?.abort();
});
</script>
<template>
  <section class="section amazon-collector">
    <div class="section-head">
      <div>
        <h2>亚马逊 · 集物自有采集</h2>
        <p>输入 ASIN 或网址，采集助手自动读取并入库</p>
      </div>
      <span class="collector-tag">自有插件 {{ collectorPluginVersion }}</span>
    </div>
    <div class="section-body">
      <p class="subtitle">
        浏览器采集 → 必要时调用服务器 Wraith → 核对商品 → 图片缓存 →
        入库。首次或地区失效时打开网页设置 10001，其余后台运行。
      </p>
      <div class="collector-tools">
        <CollectorPluginLinks /><span>{{
          wraith ? '服务器 Wraith 已配置' : '服务器 Wraith 未配置，可人工处理验证'
        }}</span>
      </div>
      <details class="details">
        <summary>首次安装及使用说明</summary>
        <ol>
          <li>下载并解压插件，在 Chrome／Edge 扩展管理开启开发者模式，加载解压文件夹。</li>
          <li>打开本系统，点击插件中的“连接当前商品库网站”，刷新系统页面。</li>
          <li>
            输入链接或拖入表格，点击开始采集。保留本页面和浏览器；离开页面将暂停，可从任务记录继续。
          </li>
          <li>
            数据接入、来源采集、图片补采和预上架共用同一个集物采集插件；插件会检查版本，过旧时先按安装／更新说明重新加载。Chrome
            与服务器 Wraith 的会话独立。
          </li>
        </ol>
      </details>
      <template v-if="writable">
        <textarea
          v-model="text"
          :disabled="busy || uploading"
          rows="5"
          placeholder="每行一个 ASIN 或 https://www.amazon.com/dp/ASIN，最多 100 个"
          aria-label="待采集 ASIN 或商品网址"
          @input="resetCreateId"
        ></textarea>
        <div
          class="collector-drop"
          :class="{ dragging }"
          @dragover.prevent="dragging = true"
          @dragleave.prevent="dragging = false"
          @drop.prevent="drop"
        >
          <input ref="fileInput" type="file" accept=".xlsx,.csv,.tsv" hidden @change="selectFile" />
          <button class="btn" :disabled="busy || uploading" @click="fileInput?.click()">
            {{ uploading ? '识别表格中…' : '选择 Excel 表格' }}
          </button>
          <span
            >也可拖拽到这里 · XLSX／CSV／TSV，最大 150
            MB；使用“ASIN”或“商品链接”列，链接保存为文本</span
          >
        </div>
        <div v-if="sheet" class="notice">
          识别 {{ sheet.valid }} 个 ASIN，去重 {{ sheet.duplicate }} 条，无效
          {{ sheet.invalid }} 条。
          <details v-if="sheet.errors.length">
            <summary>查看未采用的行</summary>
            <p v-for="(entry, i) in sheet.errors" :key="i">
              {{ entry.sheet }} 第 {{ entry.row }} 行：{{ entry.message }}
            </p>
          </details>
        </div>
        <label
          ><input v-model="includeVariants" type="checkbox" :disabled="busy" />
          同时采集该商品页面发现的子变体（每批总计最多 500 个 ASIN）</label
        >
        <div class="actions collector-actions">
          <button
            class="btn primary"
            :disabled="busy || cancelling || uploading || !text.trim()"
            @click="run()"
          >
            开始采集并入库</button
          ><button v-if="busy" class="btn" :disabled="cancelling" @click="pause">暂停采集</button
          ><button
            v-if="busy && selected && !['complete', 'cancelled'].includes(selected.state)"
            class="btn collector-cancel"
            :disabled="cancelling"
            @click="cancel(selected)"
          >
            {{ cancelling ? '取消中…' : '取消采集' }}</button
          ><button v-if="openPage" class="btn" @click="openPage()">打开网页处理</button>
        </div>
      </template>
      <p v-else class="notice">当前账号为只读成员，请使用运营或管理员账号采集。</p>
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <div v-if="selected" class="collector-progress">
        <strong
          >已采集 {{ completed }} / {{ selected.items.length }} ·
          {{ labels[selected.state] || selected.state }}</strong
        >
        <p v-if="selected.state === 'complete'">
          新建 {{ selected.result.created || 0 }} 件，更新
          {{ selected.result.updated || 0 }} 件，未完成 {{ selected.result.incomplete || 0 }} 个
          ASIN。<span v-if="selected.result.stale_roots?.length"
            >较旧结果未覆盖最新资料：{{ selected.result.stale_roots.join('、') }}</span
          >
        </p>
        <div class="collector-results">
          <p v-for="item in selected.items" :key="item.root_asin + item.asin">
            <strong>{{ item.asin }}</strong
            ><span>{{ labels[item.state] || item.state }}</span
            ><small>{{ item.message }}</small>
          </p>
        </div>
        <div class="actions">
          <RouterLink
            v-for="id in selected.result.listing_ids"
            :key="id"
            class="btn small"
            :to="'/product/' + id"
            >查看入库商品</RouterLink
          >
        </div>
      </div>
      <details v-if="jobs.length" class="details">
        <summary>我的采集任务（最近 20 批）</summary>
        <div v-for="job in jobs" :key="job.id" class="collector-history">
          <span
            >{{ new Date(job.created_at).toLocaleString('zh-CN') }} · {{ job.items.length }} 个 ASIN
            · {{ labels[job.state] }}</span
          ><button class="btn small" :disabled="busy || cancelling" @click="update(job)">
            查看</button
          ><template v-if="writable && !['complete', 'cancelled'].includes(job.state)"
            ><button class="btn small" :disabled="busy || cancelling" @click="run(job)">
              继续采集</button
            ><button class="btn small" :disabled="busy || cancelling" @click="finish(job)">
              结束并保存已采集商品</button
            ><button
              class="btn small collector-cancel"
              :disabled="cancelling || (busy && selected?.id !== job.id)"
              @click="cancel(job)"
            >
              取消采集
            </button></template
          >
        </div>
      </details>
    </div>
  </section>
</template>
<style scoped>
.amazon-collector {
  grid-column: 1 / -1;
}
.collector-cancel {
  color: #9c4148;
  background: #fbedee;
  border-color: #e5bcc0;
}
.amazon-collector .section-head {
  background: #edf4fc;
  border-radius: 16px 16px 0 0;
}
.amazon-collector .section-head p {
  margin: 6px 0 0;
  color: #526980;
}
.collector-tag {
  background: #dceaf9;
  color: #244f80;
  padding: 8px 12px;
  border-radius: 12px;
  white-space: nowrap;
}
.amazon-collector textarea {
  width: 100%;
  box-sizing: border-box;
  margin: 14px 0;
  padding: 14px;
  font: inherit;
  border: 1px solid #b9cadd;
  border-radius: 12px;
  resize: vertical;
}
.collector-tools,
.collector-drop,
.collector-history {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.collector-tools {
  margin: 14px 0;
}
.collector-drop {
  padding: 20px;
  background: #f1f6fc;
  border: 2px dashed #b5c8df;
  border-radius: 14px;
  margin-bottom: 14px;
}
.collector-drop.dragging {
  background: #dcecff;
  border-color: #3878bd;
}
.collector-drop span {
  flex: 1;
  min-width: 180px;
  color: #526980;
}
.collector-actions {
  margin: 18px 0;
}
.collector-progress {
  margin-top: 20px;
  padding: 16px;
  border-radius: 14px;
  background: #f4f7fb;
}
.collector-results {
  max-height: 320px;
  overflow: auto;
}
.collector-results p {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid #dce4ee;
}
.collector-results small {
  width: 100%;
  overflow-wrap: anywhere;
}
.collector-history {
  padding: 12px 0;
  border-bottom: 1px solid #e3eaf1;
}
@media (max-width: 600px) {
  .collector-tag {
    display: none;
  }
}
</style>
