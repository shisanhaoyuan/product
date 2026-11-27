<script setup lang="ts">
import CollectorPluginLinks from './CollectorPluginLinks.vue';
import { collectorPluginVersion } from '../utils/collectorPlugin';
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
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
import { ApiError } from '../api/client';
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
let disposed = false,
  loading = false,
  ownsExecution = false;
let pollTimer: ReturnType<typeof setTimeout> | undefined;
let heartbeatTimer: ReturnType<typeof setTimeout> | undefined;
const syncedAt = ref('');
const syncError = ref('');
const showDetails = ref(false);
const onlyIssues = ref(true);
const currentItem = computed(() =>
  selected.value?.execution?.active
    ? selected.value?.items.find((i) =>
        ['running', 'fallback_queued', 'fallback_running'].includes(i.state),
      )
    : undefined,
);
const processed = computed(
  () => selected.value?.items.filter((i) => ['done', 'failed'].includes(i.state)).length || 0,
);
const percent = computed(() =>
  selected.value?.items.length
    ? Math.floor((processed.value * 100) / selected.value.items.length)
    : 0,
);
const reviewCount = computed(() => selected.value?.items.filter((i) => i.needs_review).length || 0);
const failedCount = computed(
  () => selected.value?.items.filter((i) => i.state === 'failed').length || 0,
);
const visibleItems = computed(
  () =>
    selected.value?.items.filter(
      (i) => !onlyIssues.value || i.needs_review || i.state === 'failed',
    ) || [],
);
function jobLabel(job: CollectorJob) {
  if (job.state === 'collecting') return job.execution?.active ? '正在采集' : '等待继续';
  return labels[job.state] || job.state;
}
function stopLocalIfRemoteStopped(job: CollectorJob) {
  if (
    busy.value &&
    selected.value?.id === job.id &&
    ['paused', 'cancelled', 'complete'].includes(job.state)
  ) {
    ownsExecution = false;
    controller?.abort();
  }
}
function accept(job: CollectorJob) {
  const prior = jobs.value.find((j) => j.id === job.id);
  return !prior || (job.revision || 0) >= (prior.revision || 0);
}
async function heartbeat(id: string) {
  if (!ownsExecution || disposed) return;
  try {
    const job = await collectorApi.action(id, 'heartbeat');
    update(job);
    stopLocalIfRemoteStopped(job);
  } catch (e) {
    if (e instanceof ApiError && [403, 404, 409].includes(e.status)) {
      ownsExecution = false;
      controller?.abort();
      error.value = errorMessage(e);
    } else syncError.value = '连接中断，正在重连；超过 90 秒会释放执行权。';
  }
  if (ownsExecution && !disposed) heartbeatTimer = setTimeout(() => void heartbeat(id), 15000);
}
let createId = crypto.randomUUID();
function resetCreateId() {
  createId = crypto.randomUUID();
}
const route = useRoute();
const marketplace = ref<'US' | 'CA'>(route.query.marketplace === 'CA' ? 'CA' : 'US');
const source = computed(() =>
  (selected.value?.marketplace || marketplace.value) === 'CA' ? 'Amazon_CA' : 'Amazon',
);
const { refreshStats } = useCatalog(source);
watch(marketplace, () => {
  text.value = '';
  sheet.value = undefined;
  resetCreateId();
});
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
  collecting: '采集中',
  paused: '已暂停',
  complete: '已结束',
  cancelled: '已取消',
};
function update(job: CollectorJob) {
  if (disposed || !accept(job)) return;
  stopLocalIfRemoteStopped(job);
  selected.value = job;
  jobs.value = [job, ...jobs.value.filter((j) => j.id !== job.id)].slice(0, 20);
}
async function load() {
  if (loading || disposed) return;
  loading = true;
  try {
    const data = await collectorApi.status();
    if (disposed) return;
    jobs.value = data.jobs.map((job) =>
      accept(job) ? job : jobs.value.find((j) => j.id === job.id)!,
    );
    wraith.value = data.wraith_configured;
    const latest =
      jobs.value.find((j) => j.id === selected.value?.id) ||
      (!selected.value
        ? jobs.value.find((j) => !['complete', 'cancelled'].includes(j.state)) || jobs.value[0]
        : undefined);
    if (latest) {
      stopLocalIfRemoteStopped(latest);
      selected.value = latest;
    }
    syncedAt.value = new Date().toLocaleTimeString('zh-CN');
    syncError.value = '';
  } catch (e) {
    if (!disposed) syncError.value = '进度同步失败：' + errorMessage(e);
  } finally {
    loading = false;
  }
}
async function poll() {
  await load();
  if (!disposed) pollTimer = setTimeout(() => void poll(), 2000);
}
async function view(job: CollectorJob) {
  update(job);
  try {
    update(await collectorApi.get(job.id));
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
    sheet.value = await collectorApi.sheet(file, marketplace.value);
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
    update(
      job ||
        (await collectorApi.create(text.value, includeVariants.value, createId, marketplace.value)),
    );
    if (!job) createId = crypto.randomUUID();
    const id = selected.value!.id;
    while (!controller.signal.aborted) {
      const next = await collectorApi.action<{ task: CollectorTask | null; job: CollectorJob }>(
        id,
        'claim',
      );
      if (!['cancelled', 'complete'].includes(next.job.state)) ownsExecution = true;
      if (controller.signal.aborted) break;
      update(next.job);
      if (ownsExecution && !heartbeatTimer)
        heartbeatTimer = setTimeout(() => void heartbeat(id), 15000);
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
    if (!controller.signal.aborted) error.value = errorMessage(e);
    // Never let a losing claimant pause the device that actually owns the task.
    if (e instanceof ApiError && e.status === 409) ownsExecution = false;
  } finally {
    clearTimeout(heartbeatTimer);
    heartbeatTimer = undefined;
    if (
      ownsExecution &&
      !cancelRequested &&
      selected.value &&
      !['complete', 'cancelled'].includes(selected.value.state)
    ) {
      try {
        update(await collectorApi.action(selected.value.id, 'pause'));
      } catch {
        if (!disposed) syncError.value = '请刷新进度核对状态；已采集结果保留。';
      }
    }
    ownsExecution = false;
    busy.value = false;
    openPage.value = undefined;
    void load();
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
  void poll();
});
onBeforeUnmount(() => {
  disposed = true;
  clearTimeout(pollTimer);
  clearTimeout(heartbeatTimer);
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
        入库。首次或地区失效时打开网页设置配送邮编（US：10001；Canada：M5V 3L9），其余后台运行。
      </p>
      <div class="collector-tools">
        <CollectorPluginLinks /><span>{{
          wraith ? '服务器 Wraith 已配置' : '服务器 Wraith 未配置，可人工处理验证'
        }}</span>
      </div>
      <div v-if="selected" class="collector-progress" aria-label="采集任务进度">
        <div class="task-heading">
          <div>
            <strong>{{ selected.marketplace === 'CA' ? '加拿大站 · CAD' : '美国站 · USD' }}</strong>
            <p>
              {{ jobLabel(selected) }} ·
              {{
                busy
                  ? '本设备执行'
                  : selected.execution?.active
                    ? '其他页面或设备执行中'
                    : ['complete', 'cancelled'].includes(selected.state)
                      ? '历史任务'
                      : '可从已保存进度继续'
              }}
            </p>
          </div>
          <strong class="task-percent">{{ percent }}%</strong>
        </div>
        <progress
          :value="processed"
          :max="selected.items.length || 1"
          aria-label="商品处理进度"
        ></progress>
        <div class="task-counts">
          <span>已处理 {{ processed }} / {{ selected.items.length }}</span
          ><span>已采集 {{ completed }}</span
          ><span>待补齐 {{ reviewCount }}</span
          ><span>失败 {{ failedCount }}</span>
        </div>
        <p class="task-stage" role="status">
          {{
            currentItem
              ? currentItem.asin + ' · ' + currentItem.message
              : selected.state === 'complete'
                ? '任务已结束，采集到的商品已入库'
                : selected.state === 'cancelled'
                  ? '任务已取消，本批结果未入库'
                  : selected.state === 'paused'
                    ? '已暂停，已采集结果保留'
                    : '等待继续采集'
          }}
        </p>
        <small
          >进度按已处理 ASIN
          统计，发现子变体时总数会增加。缺价格等资料仍可入库，并标记待补齐。</small
        >
        <div
          v-if="writable && !['complete', 'cancelled'].includes(selected.state)"
          class="actions task-actions"
        >
          <button
            v-if="!busy"
            class="btn primary"
            :disabled="cancelling || selected.execution?.active"
            @click="run(selected)"
          >
            {{ selected.execution?.active ? '其他设备采集中' : '继续采集' }}
          </button>
          <button v-if="busy" class="btn" :disabled="cancelling" @click="pause">
            暂停并保留进度
          </button>
          <button v-if="openPage" class="btn primary" @click="openPage()">打开网页处理</button>
          <button
            v-if="!busy && !selected.execution?.active"
            class="btn"
            :disabled="cancelling || !completed"
            @click="finish(selected)"
          >
            结束并入库已采集商品
          </button>
          <details class="task-more">
            <summary>更多操作</summary>
            <p>取消会丢弃本批尚未入库的结果；稍后继续请使用暂停。</p>
            <button class="btn collector-cancel" :disabled="cancelling" @click="cancel(selected)">
              取消并丢弃本批结果
            </button>
          </details>
        </div>
        <p v-if="selected.state === 'complete'">
          新建 {{ selected.result.created || 0 }} 件 · 更新 {{ selected.result.updated || 0 }} 件 ·
          未完成 {{ selected.result.incomplete || 0 }} 个 ASIN
        </p>
        <div class="task-footer">
          <button class="btn small" @click="showDetails = !showDetails">
            {{ showDetails ? '收起商品明细' : '查看商品明细与缺项' }}</button
          ><span>{{ syncError || '自动同步 · ' + syncedAt }}</span
          ><button class="btn small" @click="load">刷新</button>
        </div>
        <div v-if="showDetails">
          <label><input v-model="onlyIssues" type="checkbox" />只看缺项和失败</label>
          <div class="collector-results">
            <p v-for="item in visibleItems" :key="item.root_asin + item.asin">
              <strong>{{ item.asin }}</strong
              ><span>{{ item.needs_review ? '已采集 · 待补齐' : labels[item.state] }}</span
              ><small>{{ item.message }}</small>
            </p>
            <p v-if="!visibleItems.length">暂无匹配记录</p>
          </div>
        </div>
        <details v-if="selected.result.listing_ids?.length">
          <summary>查看已入库商品</summary>
          <div class="actions">
            <RouterLink
              v-for="id in selected.result.listing_ids"
              :key="id"
              class="btn small"
              :to="(selected.marketplace === 'CA' ? '/amazon-canada-product/' : '/product/') + id"
              >查看商品</RouterLink
            >
          </div>
        </details>
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
        <label class="collector-market"
          >采集站点 / 目标商品库
          <select v-model="marketplace" :disabled="busy || uploading" aria-label="亚马逊采集站点">
            <option value="US">美国站 · amazon.com · USD</option>
            <option value="CA">加拿大站 · amazon.ca · CAD</option>
          </select>
        </label>
        <textarea
          v-model="text"
          :disabled="busy || uploading"
          rows="5"
          :placeholder="`每行一个 ASIN 或 https://www.amazon.${marketplace === 'CA' ? 'ca' : 'com'}/dp/ASIN，最多 100 个`"
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
            开始采集并入库
          </button>
        </div>
      </template>
      <p v-else class="notice">当前账号为只读成员，请使用运营或管理员账号采集。</p>
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <details v-if="jobs.length" class="details">
        <summary>历史采集任务 · 最近 {{ jobs.length }} 批</summary>
        <div v-for="job in jobs" :key="job.id" class="collector-history">
          <span
            >{{ job.marketplace || 'US' }} ·
            {{ new Date(job.created_at).toLocaleString('zh-CN') }} ·
            {{ job.items.filter((i) => i.state === 'done').length }} / {{ job.items.length }} 已采集
            · {{ jobLabel(job) }}</span
          >
          <button class="btn small" :disabled="busy || cancelling" @click="view(job)">
            查看任务
          </button>
        </div>
      </details>
    </div>
  </section>
</template>
<style scoped>
.task-heading,
.task-counts,
.task-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.task-heading p {
  margin: 8px 0;
  color: #526980;
}
.task-percent {
  font-size: 28px;
  color: #218553;
}
.collector-progress progress {
  appearance: none;
  width: 100%;
  height: 16px;
  margin: 16px 0;
  border: 0;
  border-radius: 999px;
  overflow: hidden;
  background: #e2eee7;
  accent-color: #2da568;
}
.collector-progress progress::-webkit-progress-bar {
  background: #e2eee7;
  border-radius: 999px;
}
.collector-progress progress::-webkit-progress-value {
  background: #2da568;
  border-radius: 999px;
}
.collector-progress progress::-moz-progress-bar {
  background: #2da568;
  border-radius: 999px;
}
.task-counts {
  justify-content: flex-start;
  gap: 12px 28px;
  color: #365f80;
}
.task-stage {
  padding: 14px;
  background: #e5eef8;
  border-radius: 10px;
  overflow-wrap: anywhere;
}
.task-footer {
  margin-top: 18px;
  border-top: 1px solid #dce4ee;
  padding-top: 14px;
}
.task-footer span {
  font-size: 12px;
  color: #526980;
  flex: 1;
}
.task-actions {
  margin-top: 16px;
}
.task-more {
  margin-left: auto;
  max-width: 320px;
}
.task-more summary {
  cursor: pointer;
  color: #526980;
}

.amazon-collector {
  grid-column: 1 / -1;
}
.collector-market {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-top: 14px;
  padding: 14px 16px;
  border: 1px solid #c5d7eb;
  border-radius: 12px;
  background: #edf4fc;
  color: #345779;
  font-weight: 600;
}
.collector-market select {
  flex: 1 1 260px;
  max-width: 420px;
  min-width: 0;
  min-height: 42px;
  padding: 8px 12px;
  border: 1px solid #b9cadd;
  border-radius: 8px;
  background: #fff;
  color: #264966;
  font: inherit;
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
