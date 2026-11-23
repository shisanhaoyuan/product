import { publishingApi } from '../api/publishing';
import type { GenerationJob, ListingDocument } from '../api/publishing';
import { collectListingSources } from './sourceCollection';
import { errorMessage } from './display';

export interface BatchPreparationResult {
  id: string;
  state:
    | 'waiting'
    | 'starting'
    | 'awaiting_sources'
    | 'queued'
    | 'running'
    | 'complete'
    | 'review'
    | 'failed'
    | 'skipped';
  message: string;
  jobId?: string;
  errors?: string[];
}
type Update = (result: BatchPreparationResult) => void;
export const activeGeneration = (state: string) => ['queued', 'running'].includes(state);

// Limit HTTP fan-out too; the server owns the durable generation queue and quotas.
export async function forEachBatch<T>(items: T[], work: (item: T) => Promise<void>) {
  let next = 0;
  await Promise.all(
    Array.from({ length: Math.min(2, items.length) }, async () => {
      while (next < items.length) await work(items[next++]!);
    }),
  );
}

function stopAfter(error: unknown) {
  const status = error instanceof Error && 'status' in error ? Number(error.status) : 0;
  return error instanceof TypeError || [401, 403, 429, 502, 503, 504].includes(status);
}

function validationResult(id: string, doc: ListingDocument): BatchPreparationResult {
  const entries = Object.entries(doc.validation.rows || {});
  const valid = entries.filter(([, result]) => result.valid).length;
  const errors = entries.flatMap(([sku, result]) =>
    result.errors.flatMap((issue) => {
      const details = issue.details?.length ? issue.details : [issue];
      return details.map((detail) => {
        const field =
          detail.path.replace(/^(visible|orderable|extra)\./, '').split(/[.\[]/)[0] || '';
        return `${sku} · ${doc.labels[detail.path] || doc.labels[field] || detail.path || '资料'}：${detail.message}`;
      });
    }),
  );
  const complete =
    doc.rows.length > 0 &&
    entries.length === doc.rows.length &&
    valid === doc.rows.length &&
    doc.validation.current !== false;
  return {
    id,
    state: complete ? 'complete' : 'review',
    message: `审核${complete ? '通过' : '未通过'}：${valid} / ${doc.rows.length} 个 SKU 通过`,
    errors: [...new Set(errors)],
  };
}

export function generationResult(id: string, job: GenerationJob): BatchPreparationResult {
  const rows = Object.entries(job.report.rows);
  const issues = rows.flatMap(([sku, row]) =>
    row.issues.map((issue) => `${sku}：${issue.message}`),
  );
  return {
    id,
    jobId: job.id,
    state:
      job.state === 'interrupted'
        ? 'failed'
        : job.state === 'complete' && issues.length
          ? 'review'
          : job.state,
    message: job.message,
    errors: [...new Set(issues)],
  };
}

export async function runBatchPreparation(
  ids: string[],
  mode: 'generate' | 'validate',
  signal: AbortSignal,
  update: Update,
) {
  let stopped = false;
  let sourcesStopped = false;
  const sources: { id: string; job: GenerationJob }[] = [];
  await forEachBatch([...new Set(ids)], async (id) => {
    if (stopped || signal.aborted) {
      update({ id, state: 'skipped', message: '未开始，请确认前面任务的结果后再操作' });
      return;
    }
    update({
      id,
      state: 'starting',
      message: mode === 'generate' ? '正在加入 AI 生成队列…' : '正在审核刊登资料…',
    });
    try {
      const status = await publishingApi.generation(id);
      signal.throwIfAborted();
      if (mode === 'generate') {
        if (!status.ai.configured) throw new Error('尚未配置 AI，请联系管理员');
        const job =
          status.job && ['awaiting_sources', 'queued', 'running'].includes(status.job.state)
            ? status.job
            : await publishingApi.generate(id);
        update(generationResult(id, job));
        if (job.state === 'awaiting_sources') sources.push({ id, job });
      } else {
        if (status.job && ['awaiting_sources', 'queued', 'running'].includes(status.job.state)) {
          update({ id, state: 'skipped', message: '该批次正在采集或生成，请完成后再批量审核' });
          return;
        }
        const doc = await publishingApi.read(id);
        signal.throwIfAborted();
        if (!doc) throw new Error('尚未填写刊登资料，请先执行 AI 批量填充');
        update(validationResult(id, await publishingApi.validate(id, doc.revision)));
      }
    } catch (error) {
      stopped ||= stopAfter(error) || signal.aborted;
      // A rejected enqueue must not strand already accepted source-waiting jobs.
      const quota = error instanceof Error && 'status' in error && Number(error.status) === 429;
      sourcesStopped ||= (stopAfter(error) && !quota) || signal.aborted;
      update({ id, state: 'failed', message: errorMessage(error) });
    }
  });
  // Enqueue all selected batches first. Local browser collection is serial, sharing
  // the employee's existing browser session rather than starting server browsers.
  for (const { id, job } of sources) {
    if (sourcesStopped || signal.aborted) {
      update({
        ...generationResult(id, job),
        message: '等待本机采集，可打开批次继续；未重复创建任务',
      });
      continue;
    }
    try {
      const receipts = await collectListingSources(
        job.report.source_targets || [],
        signal,
        (message) => update({ id, jobId: job.id, state: 'awaiting_sources', message }),
      );
      signal.throwIfAborted();
      update(generationResult(id, await publishingApi.resumeSources(id, job.id, receipts)));
    } catch (error) {
      sourcesStopped ||= stopAfter(error) || signal.aborted;
      update({
        id,
        jobId: job.id,
        state: 'awaiting_sources',
        message: `${errorMessage(error)}；可打开批次继续采集`,
      });
    }
  }
}
