export const MAX_IMPORT_BYTES = 20 * 1024 * 1024;
export const MAX_QUEUE_BYTES = 100 * 1024 * 1024;
export const MAX_QUEUE_FILES = 500;

export interface ImportFile {
  file: File;
  path: string;
}

// Chromium returns directory entries in pages; an empty page marks the end.
interface DropEntry {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  file: (success: (file: File) => void, failure: (error: DOMException) => void) => void;
  createReader: () => {
    readEntries: (
      success: (entries: DropEntry[]) => void,
      failure: (error: DOMException) => void,
    ) => void;
  };
}

export function selectedFiles(files: FileList | File[]): ImportFile[] {
  return Array.from(files, (file) => ({ file, path: file.webkitRelativePath || file.name }));
}

export async function droppedFiles(data: DataTransfer): Promise<ImportFile[]> {
  // Capture entries synchronously while the drag data store is still readable.
  const items = Array.from(data.items || [])
    .filter((item) => item.kind === 'file')
    .map((item) => ({
      entry: item.webkitGetAsEntry?.() as DropEntry | null,
      file: item.getAsFile(),
    }));
  const fallback = selectedFiles(data.files);
  if (!items.some((item) => item.entry)) return fallback;
  const files: ImportFile[] = [];
  async function visit(entry: DropEntry, prefix = ''): Promise<void> {
    const path = prefix + entry.name;
    if (entry.isFile) {
      if (!/\.json$/i.test(entry.name)) return;
      if (files.length >= MAX_QUEUE_FILES)
        throw new Error('一次最多选择 500 个 JSON 文件，请分批选择。');
      const file = await new Promise<File>((resolve, reject) => entry.file(resolve, reject));
      files.push({ file, path });
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      while (true) {
        const children = await new Promise<DropEntry[]>((resolve, reject) =>
          reader.readEntries(resolve, reject),
        );
        if (!children.length) break;
        for (const child of children) await visit(child, path + '/');
      }
    }
  }
  for (const { entry, file } of items) {
    if (entry) await visit(entry);
    else if (file) files.push({ file, path: file.name });
  }
  return files;
}

export function parseImport(raw: string): { raw: string; count: number } {
  const clean = raw.replace(/^\uFEFF/, '');
  let value;
  try {
    value = JSON.parse(clean);
  } catch {
    throw new Error('JSON 语法无效，未导入。');
  }
  if (
    !value ||
    typeof value !== 'object' ||
    value.schema_version !== 1 ||
    typeof value.namespace !== 'string' ||
    !value.namespace ||
    typeof value.label !== 'string' ||
    !value.label ||
    !Array.isArray(value.listings)
  ) {
    throw new Error(
      '不是商品库 ImportBundle V1：需要 schema_version、namespace、label 和 listings。',
    );
  }
  if (!value.listings.length) throw new Error('listings 中没有商品。');
  if (value.listings.length > 5000) throw new Error('单文件最多包含 5000 个商品模板。');
  return { raw: clean, count: value.listings.length };
}
