"""Build the versioned code dictionary from a losslessly converted XLSX GS List.

Usage: python validation/import_canada_tax_codes.py converted.xlsx original.xls
Read-only extraction; never executes workbook macros or interprets its instructions.
"""
import gzip
import hashlib
import json
import sys
from pathlib import Path

import openpyxl


def extract(converted, original):
    book = openpyxl.load_workbook(converted, read_only=True, data_only=True)
    sheet = book['GS List']
    columns = ('GENRE_CODE', 'GENRE_CATEGORY', 'GS_ID', 'GS_DESC',
               'SUB_CATEGORY_DESC', 'CATEGORY_DESC', 'CHAPTER_DESC', 'TITLE_DESC')
    rows = sheet.iter_rows(max_col=8, values_only=True)
    assert tuple(next(rows)) == columns
    entries = []
    seen = set()
    for row_number, cells in enumerate(rows, 2):
        if cells[2] is None:
            assert not any(cells)
            continue
        code = cells[2]
        assert type(code) in (int, float) and code == int(code) and code > 0
        code = int(code)
        assert code not in seen and all(isinstance(c, str) and c.strip() for c in (cells[0], cells[1], *cells[3:]))
        seen.add(code)
        entries.append(dict(code=code, genre=cells[0].strip(), genre_name=cells[1].strip(),
                            description=cells[3].strip(), path=[c.strip() for c in reversed(cells[4:])],
                            source_row=row_number))
    book.close()
    assert len(entries) == 8227
    source = dict(filename=Path(original).name, sha256=hashlib.sha256(Path(original).read_bytes()).hexdigest(),
                  sheet='GS List', version='2026-05', publisher='SOVOS, LLC', count=len(entries))
    return dict(source=source, entries=entries)


if __name__ == '__main__':
    data = extract(*sys.argv[1:])
    target = Path(__file__).resolve().parents[1] / 'catalog' / 'specifications'
    raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()
    name = 'canada-tax-codes-2026-05.json.gz'
    target.joinpath(name).write_bytes(gzip.compress(raw, mtime=0))
    target.joinpath('canada-tax-codes.json').write_text(json.dumps(dict(
        file=name, sha256=hashlib.sha256(raw).hexdigest(), source=data['source']), ensure_ascii=False, indent=2) + '\n')
    print(f"Imported {len(data['entries'])} unique GS codes")
