import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, catalogApi } from '../api/client';
import { useImportQueue } from './useImportQueue';
import { MAX_IMPORT_BYTES } from '../utils/importFiles';

const result = { created: 1, updated: 0, unchanged: 0, older_skipped: 0, snapshots_added: 1 };
function input(name: string, sku = name) {
  return {
    path: name,
    file: new File(
      [
        JSON.stringify({
          schema_version: 1,
          namespace: 'digitemperor',
          label: 'test',
          listings: [{ sku, price: '70.99', stock: '0' }],
        }),
      ],
      name,
    ),
  };
}
afterEach(() => vi.restoreAllMocks());
describe('batch import queue', () => {
  it('groups nested files by top-level folder and retains the batch on retry', async () => {
    const queue = useImportQueue();
    const send = vi
      .spyOn(catalogApi, 'importJson')
      .mockResolvedValueOnce(result)
      .mockRejectedValueOnce(new ApiError('Conflict', 409))
      .mockResolvedValue(result);
    await queue.addFiles([
      input('folder/a.json'),
      input('folder/sub/b.json'),
      input('other/c.json'),
    ]);
    await queue.ingest('token');
    const contexts = send.mock.calls.map((call) => call[2]!);
    expect(contexts[0].batch_id).toBe(contexts[1].batch_id);
    expect(contexts[0].batch_id).not.toBe(contexts[2].batch_id);
    expect(contexts[1].path).toBe('folder/sub/b.json');
    await queue.ingest('token', true);
    expect(send.mock.calls[3]![2]).toEqual(contexts[1]);
    queue.clear();
    await queue.addFiles([input('folder/a.json')]);
    await queue.ingest('token');
    expect(send.mock.calls[4]![2]!.batch_id).not.toBe(contexts[0].batch_id);
  });
  it('groups loose files from one submit without altering the original JSON', async () => {
    const queue = useImportQueue();
    const send = vi.spyOn(catalogApi, 'importJson').mockResolvedValue(result);
    await queue.addFiles([input('a.json'), input('b.json')]);
    const raw = queue.entries.value[0]!.raw;
    await queue.ingest('token');
    expect(send.mock.calls[0]![0]).toBe(raw);
    expect(send.mock.calls[0]![2]!.kind).toBe('files');
    expect(send.mock.calls[0]![2]!.batch_id).toBe(send.mock.calls[1]![2]!.batch_id);
  });
  it('deduplicates content across different filenames and ignores non-JSON files', async () => {
    const queue = useImportQueue();
    await queue.addFiles([
      input('a.json', 'sku-1'),
      input('copy.json', 'sku-1'),
      input('photo.png'),
    ]);
    expect(queue.entries.value).toHaveLength(1);
    expect(queue.notice.value).toContain('1 个内容重复');
    expect(queue.notice.value).toContain('1 个非 JSON');
    await queue.addFiles([input('renamed.json', 'sku-1')]);
    expect(queue.entries.value).toHaveLength(1);
  });
  it('keeps identically named but different files and preserves SKU data exactly', async () => {
    const queue = useImportQueue();
    await queue.addFiles([input('same.json', 'sku-a'), input('same.json', 'sku-b')]);
    expect(queue.pending.value).toHaveLength(2);
    expect(JSON.parse(queue.pending.value[1]!.raw!).listings).toEqual([
      { sku: 'sku-b', price: '70.99', stock: '0' },
    ]);
  });
  it('records invalid and oversized files without blocking valid files', async () => {
    const queue = useImportQueue();
    const large = input('large.json');
    Object.defineProperty(large.file, 'size', { value: MAX_IMPORT_BYTES + 1 });
    await queue.addFiles([
      { file: new File(['broken'], 'broken.json'), path: 'broken.json' },
      large,
      input('valid.json'),
    ]);
    expect(queue.entries.value.map((entry) => entry.state)).toEqual([
      'invalid',
      'invalid',
      'ready',
    ]);
    const send = vi.spyOn(catalogApi, 'importJson').mockResolvedValue(result);
    await queue.ingest('token');
    expect(send).toHaveBeenCalledTimes(1);
    expect(queue.finished.value).toHaveLength(1);
  });
  it('continues after a server validation failure and only retries failed files explicitly', async () => {
    const queue = useImportQueue();
    await queue.addFiles([input('a.json'), input('b.json'), input('c.json')]);
    const send = vi
      .spyOn(catalogApi, 'importJson')
      .mockRejectedValueOnce(new ApiError('SKU conflict', 409))
      .mockResolvedValue(result);
    await queue.ingest('token');
    expect(send).toHaveBeenCalledTimes(3);
    expect(queue.finished.value).toHaveLength(2);
    expect(queue.failed.value[0]?.message).toBe('SKU conflict');
    await queue.ingest('token');
    expect(send).toHaveBeenCalledTimes(3);
    await queue.ingest('token', true);
    expect(send).toHaveBeenCalledTimes(4);
    expect(queue.finished.value).toHaveLength(3);
    expect(queue.result.value.created).toBe(3);
  });
  it('pauses after authorization failure without submitting remaining files', async () => {
    const queue = useImportQueue();
    await queue.addFiles([input('a.json'), input('b.json')]);
    const send = vi
      .spyOn(catalogApi, 'importJson')
      .mockRejectedValue(new ApiError('Forbidden', 403));
    await queue.ingest('invalid-token');
    expect(send).toHaveBeenCalledTimes(1);
    expect(queue.entries.value.map((entry) => entry.state)).toEqual(['failed', 'ready']);
    expect(queue.error.value).toContain('已暂停');
    expect(queue.busy.value).toBe(false);
  });
  it('locks queue mutations and additional submits while a file is being imported', async () => {
    const queue = useImportQueue();
    await queue.addFiles([input('a.json')]);
    let finish!: (value: typeof result) => void;
    const send = vi.spyOn(catalogApi, 'importJson').mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const pending = queue.ingest('token');
    queue.clear();
    queue.remove(queue.entries.value[0]!.id);
    await queue.addFiles([input('b.json')]);
    await queue.ingest('token');
    expect(queue.entries.value).toHaveLength(1);
    expect(send).toHaveBeenCalledTimes(1);
    finish(result);
    await pending;
    expect(queue.finished.value).toHaveLength(1);
    queue.clear();
    expect(queue.entries.value).toHaveLength(0);
  });
});
