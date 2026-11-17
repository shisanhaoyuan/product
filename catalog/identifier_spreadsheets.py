"""Bounded, read-only spreadsheet ingestion. Codes are strings, never formulas."""
import csv
import hashlib
import io
import re
from pathlib import PurePath
from zipfile import ZipFile

from fastapi import HTTPException

from .walmart_listing_schema import identifier_errors

MAX_FILE = 8 * 1024 * 1024
MAX_ROWS = 100000
MAX_COLUMNS = 50


def code_text(value, number_format='General'):
    if isinstance(value, bool):
        return ''
    if isinstance(value, (int, float)):
        if value < 0 or value != int(value) or value >= 10**14:
            return ''
        text = str(int(value))
        if re.fullmatch('0{12,14}', number_format):
            text = text.zfill(len(number_format))
        return text
    return str(value or '').strip()


def normalized_header(value):
    return re.sub(r'[\s/＿_\-:：()（）]+', '', str(value or '')).casefold()


def spreadsheet_rows(raw, filename):
    suffix = PurePath(filename).suffix.lower()
    if not raw or len(raw) > MAX_FILE:
        raise HTTPException(422, '编码表不能为空，文件最大8MB')
    if suffix == '.xlsx':
        from openpyxl import load_workbook
        try:
            with ZipFile(io.BytesIO(raw)) as archive:
                if len(archive.infolist()) > 1000 or sum(v.file_size for v in archive.infolist()) > 80*1024*1024:
                    raise HTTPException(422, 'Excel 解压内容过大，请拆分表格')
                if any('vbaProject' in n for n in archive.namelist()):
                    raise HTTPException(422, '不接收包含宏的编码文件')
            workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
            try:
                scanned = 0
                for sheet in workbook:
                    if sheet.max_column and sheet.max_column > MAX_COLUMNS:
                        raise HTTPException(422, '编码表最多50列，请仅保留相关数据')
                    # Do not trust declared row counts or silently truncate.
                    for index, row in enumerate(sheet.iter_rows(max_col=MAX_COLUMNS), 1):
                        scanned += 1
                        if scanned > MAX_ROWS + 20:
                            raise HTTPException(422, '编码表最多100000行，请拆分导入')
                        yield sheet.title, index, [(c.value, c.data_type, c.number_format) for c in row]
            finally:
                workbook.close()
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(422, '无法读取 XLSX，请使用未加密的标准 Excel 文件') from None
    elif suffix in ('.csv', '.tsv'):
        try:
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('gb18030')
            for index, row in enumerate(csv.reader(io.StringIO(text), delimiter='\t' if suffix == '.tsv' else ','), 1):
                if index > MAX_ROWS+1 or len(row) > MAX_COLUMNS:
                    raise HTTPException(422, '编码表最多100000行、50列，请拆分导入')
                yield 'CSV', index, [(v, 's', 'General') for v in row]
        except (UnicodeError, csv.Error):
            raise HTTPException(422, '无法读取 CSV/TSV，请使用 UTF-8 编码') from None
    else:
        raise HTTPException(422, '支持 .xlsx、.csv、.tsv；旧版 .xls 请先另存为 .xlsx')


def parse_spreadsheet(raw, filename, brand='', proof=''):
    filename = filename.replace('\\', '/').rsplit('/', 1)[-1][:200]
    items = []; seen = set(); columns = {}; sheets = []; errors = []
    total = duplicate = invalid = blank = 0
    for sheet, index, row in spreadsheet_rows(raw, filename):
        populated = [(i,v) for i,v in enumerate(row) if v[0] is not None and str(v[0]).strip()]
        if not populated:
            continue
        if sheet not in columns:
            headers = [(i,v[0]) for i,v in populated if normalized_header(v[0]) in (
                'ean','ean码','ean编码','upc','upc码','upc编码','upcean','upcean码','upcean编码',
                'gtin','gtin码','gtin编码','商品编码','条码','条形码','编码')]
            if len(headers) > 1:
                raise HTTPException(422, f'{sheet} 有多个编码列，请仅保留一个需要导入的编码列')
            if headers:
                columns[sheet] = headers[0][0]; sheets.append(dict(sheet=sheet,column=columns[sheet]+1))
                continue
            # Permit one-column code-only files without a header. Never mistake
            # a serial number / ASIN / marketing text for a code column.
            if len(populated) == 1 and re.fullmatch('[0-9]{12,14}', code_text(populated[0][1][0],populated[0][1][2])):
                columns[sheet] = populated[0][0]; sheets.append(dict(sheet=sheet,column=columns[sheet]+1))
            elif index <= 10:
                continue
            else:
                raise HTTPException(422, f'{sheet} 未找到 EAN / UPC / GTIN 编码列，请添加列标题')
        cell = row[columns[sheet]] if columns[sheet] < len(row) else (None,'s','General')
        value = code_text(cell[0],cell[2])
        if not value and not cell[0]:
            blank += 1; continue
        total += 1
        kind = {12:'UPC',13:'EAN',14:'GTIN'}.get(len(value),'EAN')
        if cell[1] == 'f' or value.startswith(('=','+','@')) or identifier_errors(dict(mode='identifier',type=kind,value=value)):
            invalid += 1
            if len(errors) < 20:
                errors.append(dict(sheet=sheet,row=index,message='编码格式、长度或校验位不正确；公式不导入'))
            continue
        key = value.zfill(14)
        if key in seen:
            duplicate += 1; continue
        seen.add(key)
        items.append(dict(type=kind,value=value,brand=brand.strip(),proof=proof.strip() or f'用户提供编码表：{filename}',
                          source_system='',source_sku=''))
    if not items:
        raise HTTPException(422, '没有可导入的有效编码；请检查编码列、文本格式和校验位')
    return items, dict(filename=filename,digest=hashlib.sha256(raw).hexdigest(),total=total,
                      valid=len(items),duplicate=duplicate,invalid=invalid,blank=blank,errors=errors,
                      columns=sheets,sample=[v['value'] for v in items[:5]])
