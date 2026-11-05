import { computed, ref } from 'vue';
import { ApiError, catalogApi } from '../api/client';
import type { ImportResult } from '../api/types';
import { errorMessage } from '../utils/display';
import {
  MAX_IMPORT_BYTES,
  MAX_QUEUE_BYTES,
  MAX_QUEUE_FILES,
  parseImport,
  type ImportFile,
} from '../utils/importFiles';

type ImportState = 'ready' | 'invalid' | 'importing' | 'success' | 'failed';
export interface QueueEntry {
  id: number;
  path: string;
  bytes: number;
  count: number;
  state: ImportState;
  message: string;
  raw?: string;
  digest?: string;
  result?: ImportResult;
}

export function useImportQueue() {
  const entries = ref<QueueEntry[]>([]);
  const reading = ref(false),
    busy = ref(false),
    error = ref(''),
    notice = ref('');
  let nextId = 0;
  const locked = computed(() => reading.value || busy.value);
  const pending = computed(() => entries.value.filter((entry) => entry.state === 'ready'));
  const failed = computed(() => entries.value.filter((entry) => entry.state === 'failed'));
  const finished = computed(() => entries.value.filter((entry) => entry.state === 'success'));
  const result = computed(() =>
    finished.value.reduce<ImportResult>(
      (sum, entry) => {
        for (const key of Object.keys(sum) as (keyof ImportResult)[])
          sum[key] += entry.result?.[key] || 0;
        return sum;
      },
      { created: 0, updated: 0, unchanged: 0, older_skipped: 0, snapshots_added: 0 },
    ),
  );

  async function addFiles(files: ImportFile[]) {
    if (locked.value) return;
    reading.value = true;
    error.value = '';
    notice.value = '';
    let ignored = 0,
      duplicate = 0;
    let bytes = entries.value.reduce((sum, entry) => sum + (entry.raw ? entry.bytes : 0), 0);
    const hashes = new Set(entries.value.map((entry) => entry.digest).filter(Boolean));
    try {
      for (const { file, path } of files) {
        if (!/\.json$/i.test(file.name)) {
          ignored++;
          continue;
        }
        if (entries.value.length >= MAX_QUEUE_FILES) {
          error.value = '队列最多容纳 500 个 JSON 文件，请清空或分批导入。';
          break;
        }
        const entry: QueueEntry = {
          id: ++nextId,
          path,
          bytes: file.size,
          count: 0,
          state: 'ready',
          message: '',
        };
        try {
          if (file.size > MAX_IMPORT_BYTES) throw new Error('文件超过 20 MB，未导入。');
          if (bytes + file.size > MAX_QUEUE_BYTES)
            throw new Error('队列内容超过 100 MB，请分批导入。');
          const raw = await file.text();
          const digest = Array.from(
            new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw))),
            (byte) => byte.toString(16).padStart(2, '0'),
          ).join('');
          if (hashes.has(digest)) {
            duplicate++;
            continue;
          }
          const parsed = parseImport(raw);
          Object.assign(entry, parsed, { digest });
          hashes.add(digest);
          bytes += file.size;
        } catch (reason) {
          entry.state = 'invalid';
          entry.message = errorMessage(reason);
        }
        entries.value.push(entry);
      }
      notice.value = [
        ignored ? `已忽略 ${ignored} 个非 JSON 文件` : '',
        duplicate ? `已跳过 ${duplicate} 个内容重复的文件` : '',
      ]
        .filter(Boolean)
        .join('；');
    } finally {
      reading.value = false;
    }
  }

  async function ingest(token: string, retry = false) {
    if (locked.value) return;
    const selected = retry ? [...failed.value] : [...pending.value];
    if (!selected.length) return;
    busy.value = true;
    error.value = '';
    try {
      // Each file uses the existing server transaction; never retry a write automatically.
      for (const entry of selected) {
        entry.state = 'importing';
        entry.message = '';
        try {
          entry.result = await catalogApi.importJson(entry.raw!, token);
          entry.state = 'success';
        } catch (reason) {
          entry.state = 'failed';
          entry.message = errorMessage(reason);
          try {
            const details = JSON.parse(entry.message);
            if (Array.isArray(details))
              entry.message = details
                .map(
                  (detail) =>
                    `${Array.isArray(detail.loc) ? detail.loc.join(' / ') + '：' : ''}${detail.msg || '字段无效'}`,
                )
                .join('；');
          } catch {
            /* Keep plain-text failures unchanged. */
          }
          if (reason instanceof ApiError && [401, 403].includes(reason.status)) {
            error.value = '身份验证或导入权限失败，已暂停后续文件。请检查登录状态和导入令牌。';
            break;
          }
        }
      }
    } finally {
      busy.value = false;
    }
  }

  function remove(id: number) {
    if (!locked.value) entries.value = entries.value.filter((entry) => entry.id !== id);
  }
  function clear() {
    if (locked.value) {
      return;
    }
    entries.value = [];
    error.value = '';
    notice.value = '';
  }
  return {
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
  };
}
