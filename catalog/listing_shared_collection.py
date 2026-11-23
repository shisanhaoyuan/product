"""Consume this filling run's catalog captures without starting another browser session."""
import json
import re
import time
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .database import write_lock
from .prelisting import encoded


class SourceReceipts(BaseModel):
    captures: dict[str, str] = Field(max_length=5000)


def source_targets(basis, statuses):
    from .walmart_publishing import EDITABLE
    return [dict(platform_sku=v['platform_sku'],listing_id=i['listing_id'],
                 sku=v.get('capture_sku') or '',asin=v['asin'])
            for i in basis['items'] if i.get('source')=='Amazon' for v in i['variants']
            if statuses.get(v['platform_sku'],{}).get('status','unsubmitted') in EDITABLE]


def checked_receipts(db, job, report, receipts):
    targets=report.get('source_targets',[])
    if set(receipts)!={t['platform_sku'] for t in targets}:
        raise HTTPException(409,'本轮采集尚未完成，请继续采集当前批次所有子商品')
    result={}
    for target in targets:
        row=db.execute('SELECT * FROM source_captures WHERE id=?',
                       (receipts[target['platform_sku']],)).fetchone()
        if (not row or row['listing_id']!=target['listing_id'] or row['variant_sku']!=target['sku']
                or row['asin']!=target['asin'] or row['actor_id']!=job['actor_id']
                or datetime.fromisoformat(row['captured_at']).timestamp()*1000 < job['started_at']):
            raise HTTPException(409,'采集记录不属于本轮商品和操作者，请重新采集')
        # A concurrently captured quote must not silently replace the acknowledged one in pricing.
        newest=db.execute('SELECT id FROM source_captures WHERE listing_id=? AND asin=? ORDER BY captured_at DESC,id DESC LIMIT 1',
                          (target['listing_id'],target['asin'])).fetchone()
        if not newest or newest['id']!=row['id']:
            raise HTTPException(409,'采集记录已被更新，请重新采集后继续')
        quote=json.loads(row['data_json'])
        if quote.get('status') not in ('captured','partial') or not re.search(r'\b10001\b',quote.get('delivery_location') or ''):
            raise HTTPException(409,'请在商品库使用的浏览器中设置邮编 10001，再继续采集')
        result[target['platform_sku']]=quote
    # Only this operator's captures may change the source overlay during the handoff.
    start=datetime.fromtimestamp(job['started_at']/1000,timezone.utc).isoformat()
    for lid,baseline in report.get('source_digests',{}).items():
        expected=baseline
        allowed={(t['sku'],t['asin']) for t in targets if t['listing_id']==lid}
        if allowed:
            for capture in db.execute('SELECT * FROM source_captures WHERE listing_id=? AND captured_at>=? ORDER BY captured_at,id',(lid,start)):
                data=json.loads(capture['data_json'])
                if (capture['actor_id']!=job['actor_id'] or (capture['variant_sku'],capture['asin']) not in allowed
                        or data.get('source_digest_before')!=expected):
                    raise HTTPException(409,'采集期间商品库资料被其他操作修改，请刷新来源后重试')
                expected=data.get('source_digest_after')
        current=db.execute('SELECT digest FROM current_listings WHERE id=?',(lid,)).fetchone()
        if not current or current[0]!=expected:
            raise HTTPException(409,'采集期间商品库资料被修改，请刷新来源后重试')
    return result


def resume_sources(catalog, publisher, bid, jid, receipts, actor_id=None, ai=None, schedule=True):
    from .listing_generation_jobs import checked_context, public_job, schedule_job
    from .listing_ai_client import ListingAIClient, ListingAIError
    ai=ai or ListingAIClient()
    with catalog.connect() as db:
        write_lock(db)
        row=db.execute('SELECT * FROM listing_generation_jobs WHERE id=? AND batch_id=?',(jid,bid)).fetchone()
        if not row or row['actor_id']!=actor_id:
            raise HTTPException(409,'该采集任务不属于当前批次或操作者')
        report=json.loads(row['report_json'])
        if row['state']!='awaiting_sources':
            if report.get('source_capture_ids')==receipts and row['state'] in ('queued','running','complete'):
                return public_job(row)
            raise HTTPException(409,'采集任务状态已变化，请刷新页面')
        try:
            checked_context(db,dict(row),check_sources=False)
        except ListingAIError as exc:
            raise HTTPException(409,str(exc)) from None
        checked_receipts(db,row,report,receipts)
        from .listing_preparation_source import refresh_amazon
        from .walmart_publishing import document
        stored=refresh_amazon(catalog,db,document(db,bid),actor_id,jid)
        db.execute('UPDATE listing_generation_jobs SET revision=? WHERE id=?',(stored['revision'],jid))
        report['source_capture_ids']=receipts
        db.execute("UPDATE listing_generation_jobs SET state='queued',report_json=?,message=?,updated_at=? WHERE id=?",
                   (encoded(report),'本轮商品库采集完成，开始计算售价和生成资料',int(time.time()),jid))
        row=db.execute('SELECT * FROM listing_generation_jobs WHERE id=?',(jid,)).fetchone()
    if schedule:
        schedule_job(catalog,publisher,jid,ai)
    return public_job(row)


def apply_sources(catalog, job, report, progress, checked_context):
    from .listing_preparation_source import refresh_amazon
    with catalog.connect() as db:
        write_lock(db)
        stored,_=checked_context(db,job)
        captures=checked_receipts(db,job,report,report.get('source_capture_ids',{}))
        for target in report.get('source_targets',[]):
            sku=target['platform_sku'];quote=captures[sku]
            outcome=dict(state=quote['status'],asin=target['asin'],method=quote.get('method'),
                         message='已使用本轮商品库采集结果，配送邮编 10001；未启动其他浏览器',
                         capture_id=quote['capture_id'],facts=quote.get('listing_facts',{}),
                         image_collection=quote.get('image_collection',{}))
            entry=report['rows'].setdefault(sku,dict(state='running',preparation_state='collecting',issues=[],fields={},rounds=[]))
            entry['source_collection']=outcome
            if quote.get('image_collection'):
                entry['image_collection']=quote['image_collection']
            stored=refresh_amazon(catalog,db,stored,job.get('actor_id'),job['id'],sku)
            job['revision']=stored['revision']
        db.execute('UPDATE listing_generation_jobs SET revision=?,report_json=?,updated_at=? WHERE id=?',
                   (job['revision'],encoded(report),int(time.time()),job['id']))
        return stored
