"""Read ASIN/URL columns without evaluating Excel formulas or fetching links."""
from .identifier_spreadsheets import spreadsheet_rows, normalized_header
from .amazon_import import parse_inputs
from fastapi import HTTPException
import threading

MAX_FILE=150*1024*1024
_PARSE_GATE=threading.BoundedSemaphore(2)

HEADERS={'asin','商品asin','父asin','子asin','url','链接','网址','商品链接','商品网址','亚马逊链接','amazonurl','producturl'}


def parse_sheet(raw,filename,marketplace="US"):
    if not _PARSE_GATE.acquire(blocking=False):
        raise HTTPException(429,'当前有两个采集表正在解析，请稍后重新导入')
    try:
        return _parse_sheet(raw,filename,marketplace)
    finally:
        _PARSE_GATE.release()


def _parse_sheet(raw,filename,marketplace="US"):
    asins=[];seen=set();columns={};errors=[];duplicate=0;invalid=0;scanned=0
    try:
        for sheet,index,row in spreadsheet_rows(raw,filename,max_file=MAX_FILE,max_uncompressed=512*1024*1024):
            scanned+=1
            if scanned>10000:raise HTTPException(422,'采集表最多读取 10000 行，请拆分文件')
            populated=[(i,c) for i,c in enumerate(row) if c[0] is not None and str(c[0]).strip()]
            if not populated:continue
            if sheet not in columns:
                headings=[i for i,c in populated if normalized_header(c[0]) in HEADERS]
                if headings:columns[sheet]=headings;continue
                if len(populated)==1:
                    try:parse_inputs(str(populated[0][1][0]),marketplace)
                    except HTTPException:continue
                    columns[sheet]=[populated[0][0]]
                elif index<=10:continue
                else:raise HTTPException(422,'表格未找到 ASIN／商品链接列，请添加列标题')
            for col in columns[sheet]:
                value,kind,_=row[col] if col < len(row) else (None, None, None)
                if value is None or not str(value).strip():continue
                try:
                    if kind=='f' or str(value).startswith(('=','+','@')):raise HTTPException(422,'不执行公式，请将链接粘贴为文本')
                    values=parse_inputs(str(value),marketplace)
                    for asin in values:
                        if asin in seen:duplicate+=1;continue
                        seen.add(asin);asins.append(asin)
                    if len(asins)>100:raise HTTPException(413,'每批最多 100 个 ASIN，请拆分表格')
                except HTTPException as exc:
                    if exc.status_code==413:raise
                    invalid+=1
                    if len(errors)<20:errors.append(dict(sheet=sheet,row=index,message=exc.detail))
    except HTTPException as exc:
        exc.detail=str(exc.detail).replace('编码表','采集表').replace('编码文件','采集文件')
        raise
    if not asins:raise HTTPException(422,'没有找到有效 ASIN 或所选站点亚马逊商品链接；请使用 ASIN／商品链接列，链接以文本保存')
    return dict(asins=asins,valid=len(asins),duplicate=duplicate,invalid=invalid,errors=errors)
