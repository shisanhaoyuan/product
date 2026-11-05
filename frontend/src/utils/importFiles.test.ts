import { describe, expect, it } from 'vitest';
import { droppedFiles, parseImport, selectedFiles } from './importFiles';

const bundle = {
  schema_version: 1,
  namespace: 'digitemperor',
  label: 'test',
  listings: [{ sku: 'child-1' }],
};
describe('import file preparation', () => {
  it('accepts UTF-8 BOM without changing prices, SKU, or content', () => {
    const raw = JSON.stringify({
      ...bundle,
      listings: [{ sku: 'child-2', price: '84.99', stock: '0' }],
    });
    expect(parseImport('\uFEFF' + raw)).toEqual({ raw, count: 1 });
  });
  it('rejects raw audit logs, invalid JSON and empty bundles', () => {
    expect(() => parseImport('{')).toThrow('JSON 语法无效');
    expect(() => parseImport('{"erp_fill_audit":{}}')).toThrow('ImportBundle V1');
    expect(() => parseImport(JSON.stringify({ ...bundle, listings: [] }))).toThrow('没有商品');
  });
  it('keeps selected folder paths for identically named files', () => {
    const file = new File(['{}'], 'same.json');
    Object.defineProperty(file, 'webkitRelativePath', { value: 'folder/nested/same.json' });
    expect(selectedFiles([file])[0]?.path).toBe('folder/nested/same.json');
  });
  it('recursively drains all directory reader pages and skips other file types', async () => {
    const leaf = (name: string) => ({
      name,
      isFile: true,
      isDirectory: false,
      file: (done: (file: File) => void) => done(new File(['{}'], name)),
    });
    const directory = (name: string, pages: unknown[][]) => ({
      name,
      isFile: false,
      isDirectory: true,
      createReader: () => ({
        readEntries: (done: (entries: unknown[]) => void) => done(pages.shift() || []),
      }),
    });
    const root = directory('exports', [
      Array.from({ length: 100 }, (_, i) => leaf(`${i}.json`)),
      [leaf('extra.JSON'), leaf('ignore.txt'), directory('nested', [[leaf('same.json')], []])],
      [],
    ]);
    const data = {
      items: [{ kind: 'file', webkitGetAsEntry: () => root, getAsFile: () => null }],
      files: [],
    } as unknown as DataTransfer;
    const result = await droppedFiles(data);
    expect(result).toHaveLength(102);
    expect(result.at(-1)?.path).toBe('exports/nested/same.json');
    expect(result.at(-2)?.path).toBe('exports/extra.JSON');
  });
  it('falls back to dropped files when directory API is unavailable', async () => {
    const file = new File(['{}'], 'example.json');
    expect(await droppedFiles({ items: [], files: [file] } as unknown as DataTransfer)).toEqual([
      { file, path: 'example.json' },
    ]);
  });
});
