import { beforeEach, expect, it, vi } from 'vitest';
import type { GenerationJob, ListingDocument } from '../api/publishing';
import { generationResult, runBatchPreparation } from './batchPreparation';
import type { BatchPreparationResult } from './batchPreparation';

const mock = vi.hoisted(() => ({
  generation: vi.fn(),
  generate: vi.fn(),
  read: vi.fn(),
  validate: vi.fn(),
  resumeSources: vi.fn(),
  collect: vi.fn(),
  submit: vi.fn(),
}));
vi.mock('../api/publishing', () => ({ publishingApi: mock }));
vi.mock('./sourceCollection', () => ({ collectListingSources: mock.collect }));

function job(id: string, state: GenerationJob['state'] = 'queued'): GenerationJob {
  return {
    id: 'job-' + id,
    batch_id: id,
    revision: 3,
    state,
    message: state,
    report: {
      rows: {},
      source_targets: [{ listing_id: id, platform_sku: 'sku-' + id, sku: id, asin: 'B000000001' }],
    },
  };
}
function doc(): ListingDocument {
  return {
    revision: 7,
    rows: [{ platform_sku: 'sku-a' }, { platform_sku: 'sku-b' }],
    labels: { color: '颜色' },
    validation: {
      current: true,
      rows: {
        'sku-a': { valid: true, errors: [] },
        'sku-b': { valid: true, errors: [] },
      },
    },
  } as unknown as ListingDocument;
}
function record() {
  const results = new Map<string, BatchPreparationResult>();
  return { results, update: (result: BatchPreparationResult) => results.set(result.id, result) };
}
beforeEach(() => {
  vi.resetAllMocks();
  mock.generation.mockResolvedValue({ ai: { configured: true }, job: null });
  mock.generate.mockImplementation(async (id: string) => job(id));
  mock.read.mockResolvedValue(doc());
  mock.validate.mockResolvedValue(doc());
  mock.collect.mockResolvedValue({ 'sku-a': 'capture-a' });
  mock.resumeSources.mockImplementation(async (id: string) => job(id));
});

it('queues all batches before local capture, reuses an active job, and hands receipts to the correct batch', async () => {
  mock.generation.mockImplementation(async (id: string) => ({
    ai: { configured: true },
    job: id === 'b' ? job('b', 'running') : null,
  }));
  mock.generate.mockImplementation(async (id: string) =>
    job(id, id === 'a' ? 'awaiting_sources' : 'queued'),
  );
  mock.collect.mockImplementation(async () => {
    expect(mock.generate.mock.calls.map((call) => call[0])).toEqual(['a', 'c']);
    return { 'sku-a': 'capture-a' };
  });
  const { results, update } = record();
  await runBatchPreparation(['a', 'b', 'c', 'a'], 'generate', new AbortController().signal, update);
  expect(results.get('a')?.state).toBe('queued');
  expect(results.get('b')?.state).toBe('running');
  expect(results.get('c')?.state).toBe('queued');
  expect(mock.collect.mock.calls[0]![0]).toEqual(job('a').report.source_targets);
  expect(mock.resumeSources).toHaveBeenCalledWith('a', 'job-a', { 'sku-a': 'capture-a' });
  expect(mock.submit).not.toHaveBeenCalled();
});

it('limits concurrent batch requests to two while enqueuing the rest', async () => {
  let active = 0,
    peak = 0;
  const pending: (() => void)[] = [];
  mock.generation.mockImplementation(
    () =>
      new Promise((resolve) => {
        active++;
        peak = Math.max(peak, active);
        pending.push(() => {
          active--;
          resolve({ ai: { configured: true }, job: null });
        });
      }),
  );
  const running = runBatchPreparation(
    ['a', 'b', 'c', 'd', 'e'],
    'generate',
    new AbortController().signal,
    vi.fn(),
  );
  await vi.waitFor(() => expect(pending).toHaveLength(2));
  for (let i = 0; i < 5; i++) {
    await vi.waitFor(() => expect(pending[i]).toBeTypeOf('function'));
    pending[i]!();
  }
  await running;
  expect(peak).toBe(2);
  expect(mock.generate).toHaveBeenCalledTimes(5);
});

it('keeps validation failures attached to their SKU and explains missing fields in Chinese', async () => {
  const invalid = doc();
  invalid.validation.rows!['sku-b'] = {
    valid: false,
    errors: [{ path: 'visible.color', message: '请填写颜色' }],
  };
  mock.validate.mockImplementation(async (id: string) => (id === 'a' ? doc() : invalid));
  const { results, update } = record();
  await runBatchPreparation(['a', 'b'], 'validate', new AbortController().signal, update);
  expect(results.get('a')?.state).toBe('complete');
  expect(results.get('b')).toMatchObject({
    state: 'review',
    message: '审核未通过：1 / 2 个 SKU 通过',
    errors: ['sku-b · 颜色：请填写颜色'],
  });
  expect(mock.validate).toHaveBeenCalledWith('a', 7);
  expect(mock.generate).not.toHaveBeenCalled();
  expect(mock.submit).not.toHaveBeenCalled();
});

it('skips generating batches during validation and reports unprepared batches individually', async () => {
  mock.generation.mockImplementation(async (id: string) => ({
    ai: { configured: false },
    job: id === 'a' ? job('a', 'running') : null,
  }));
  mock.read.mockImplementation(async (id: string) => (id === 'b' ? null : doc()));
  const { results, update } = record();
  await runBatchPreparation(['a', 'b', 'c'], 'validate', new AbortController().signal, update);
  expect(results.get('a')?.state).toBe('skipped');
  expect(results.get('b')?.message).toContain('尚未填写');
  expect(results.get('c')?.state).toBe('complete');
  expect(mock.validate).toHaveBeenCalledTimes(1);
});

it('stops later requests after an unknown gateway outcome and never replays a write', async () => {
  mock.generate.mockRejectedValue(Object.assign(new Error('结果未知'), { status: 504 }));
  const { results, update } = record();
  await runBatchPreparation(['a', 'b', 'c', 'd'], 'generate', new AbortController().signal, update);
  expect(mock.generate.mock.calls.map((call) => call[0])).toEqual(['a', 'b']);
  expect(results.get('c')?.state).toBe('skipped');
  expect(results.get('d')?.state).toBe('skipped');
});

it('preserves source-waiting jobs when collection is cancelled and does not send stale receipts', async () => {
  const controller = new AbortController();
  mock.generate.mockImplementation(async (id: string) => job(id, 'awaiting_sources'));
  mock.collect.mockImplementation(async () => {
    controller.abort();
    return {};
  });
  const { results, update } = record();
  await runBatchPreparation(['a', 'b'], 'generate', controller.signal, update);
  expect(mock.generate).toHaveBeenCalledTimes(2);
  expect(mock.collect).toHaveBeenCalledTimes(1);
  expect(mock.resumeSources).not.toHaveBeenCalled();
  expect(results.get('a')?.state).toBe('awaiting_sources');
  expect(results.get('b')?.message).toContain('打开批次继续');
});

it('continues collection for accepted jobs when the remaining queue is full', async () => {
  mock.generate.mockImplementation(async (id: string) => {
    if (id === 'a') return job(id, 'awaiting_sources');
    throw Object.assign(new Error('队列已满'), { status: 429 });
  });
  const { results, update } = record();
  await runBatchPreparation(['a', 'b', 'c'], 'generate', new AbortController().signal, update);
  expect(results.get('c')?.state).toBe('skipped');
  expect(mock.collect).toHaveBeenCalledTimes(1);
  expect(mock.resumeSources).toHaveBeenCalledWith('a', 'job-a', { 'sku-a': 'capture-a' });
  expect(results.get('a')?.state).toBe('queued');
});

it('shows AI completion issues and refuses to start unconfigured generation', async () => {
  const complete = job('a', 'complete');
  complete.report.rows = {
    SKU: { state: 'review', issues: [{ path: 'color', message: '缺少颜色' }], fields: {} },
  };
  expect(generationResult('a', complete)).toMatchObject({
    state: 'review',
    errors: ['SKU：缺少颜色'],
  });
  mock.generation.mockResolvedValue({ ai: { configured: false }, job: null });
  const { results, update } = record();
  await runBatchPreparation(['a'], 'generate', new AbortController().signal, update);
  expect(results.get('a')?.message).toContain('尚未配置 AI');
  expect(mock.generate).not.toHaveBeenCalled();
});
