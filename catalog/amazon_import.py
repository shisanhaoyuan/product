"""Own-browser collection jobs. The server plans tools, validates facts and imports.

No model receives browser credentials or decides URLs/code. Browser work stays on
operators' computers; the existing Wraith worker is a bounded server fallback.
"""
import asyncio
import hashlib
import json
import re
import time
import tempfile
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .database import write_lock
from .models import ImportBundle, Listing, Variant
from .source_capture import Document, amazon_source, save_capture
from .listing_source_evidence import amazon_details
from .images import amazon_images

NAMESPACE = 'amazon-browser-us'
MAX_ROWS = 500


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS amazon_import_jobs(
        id TEXT PRIMARY KEY, actor_id INTEGER NOT NULL, created_at TEXT NOT NULL,
        include_variants INTEGER NOT NULL, state TEXT NOT NULL, result_json TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS amazon_import_items(
        job_id TEXT NOT NULL REFERENCES amazon_import_jobs(id), root_asin TEXT NOT NULL,
        asin TEXT NOT NULL, state TEXT NOT NULL, token TEXT NOT NULL, lease_until BIGINT NOT NULL,
        data_json TEXT NOT NULL, message TEXT NOT NULL,
        PRIMARY KEY(job_id,root_asin,asin))''')
    db.execute('CREATE INDEX IF NOT EXISTS amazon_import_queue ON amazon_import_items(state,lease_until)')
    db.execute('INSERT INTO migrations VALUES(26) ON CONFLICT DO NOTHING')


def parse_inputs(text):
    result=[]
    for word in re.split(r'[\s,，;；]+',text.strip()):
        if not word:continue
        asin=word.upper()
        if not re.fullmatch('[A-Z0-9]{10}',asin):
            try:
                u=urlsplit(word)
                if u.scheme!='https' or u.netloc not in ('www.amazon.com','amazon.com') or u.username or u.password:
                    raise ValueError()
                match=re.fullmatch(r'/(?:[^/]+/)?dp/([A-Za-z0-9]{10})(?:/ref=[^/]*)?/?|/gp/product/([A-Za-z0-9]{10})(?:/ref=[^/]*)?/?',u.path)
                if not match:raise ValueError()
                asin=(match[1] or match[2]).upper()
            except ValueError:
                raise HTTPException(422,'请输入 10 位 ASIN 或完整的 amazon.com 商品链接；当前采集美国站') from None
        if asin not in result:result.append(asin)
    if not 1<=len(result)<=100:
        raise HTTPException(422,'每批请输入 1–100 个不同的 ASIN 或商品网址')
    return result


def actor(request):
    return (getattr(request.state,'user',None) or {}).get('id',0)


def owned(db,jid,uid):
    job=db.execute('SELECT * FROM amazon_import_jobs WHERE id=? AND actor_id=?',(jid,uid)).fetchone()
    if not job:raise HTTPException(404,'采集任务不存在或不属于当前账号')
    return dict(job)


def public_job(db,jid,uid):
    job=owned(db,jid,uid)
    rows=[{k:r[k] for k in ('root_asin','asin','state','message')} for r in db.execute(
        'SELECT * FROM amazon_import_items WHERE job_id=? ORDER BY root_asin,asin',(jid,))]
    return {**{k:job[k] for k in ('id','created_at','state')},'items':rows,'result':{k:v for k,v in json.loads(job['result_json']).items() if not k.startswith('_')}}


def active_actor(db,uid):
    if uid:
        user=db.execute('SELECT active,role FROM users WHERE id=?',(uid,)).fetchone()
        if not user or not user['active'] or user['role'] not in ('admin','editor'):
            raise HTTPException(403,'账号已停用或不再拥有采集权限')


def target(db,jid,uid,root,asin,token):
    active_actor(db,uid)
    job=owned(db,jid,uid)
    row=db.execute('SELECT * FROM amazon_import_items WHERE job_id=? AND root_asin=? AND asin=?',
                   (jid,root,asin)).fetchone()
    if not row or row['token']!=token or not token or job['state']!='collecting':
        raise HTTPException(409,'任务归属或执行凭据已变化，请刷新采集进度')
    if row['state']!='done' and row['lease_until']<time.time():
        raise HTTPException(409,'任务已过期，请继续采集后重试')
    return job,dict(row)


def verified_page(catalog,root,asin,html,url,method):
    if urlsplit(url).scheme!='https' or urlsplit(url).netloc!='www.amazon.com' or not re.fullmatch(
            r'/(?:[^/]+/)?dp/'+asin+r'/?',urlsplit(url).path):
        raise HTTPException(422,'返回页面与任务 ASIN 不一致')
    try:
        facts=amazon_details(html,asin)
        if not facts['content.copy']['title'].strip():raise ValueError('未取得当前商品标题，不能入库')
        quote=amazon_source(html,asin)
        if not re.search(r'\b10001\b',quote.get('delivery_location') or ''):
            raise ValueError('请在浏览器设置配送邮编 10001 后重新采集')
        if quote['status']=='browser_required':raise ValueError('请等待普通新品报价列表加载后重新采集')
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    doc=Document(html).root
    variants=[]
    for n in doc.ids('jiwu-variants'):
        try:
            values=json.loads(n.text())
            if isinstance(values,list):
                variants.extend(v for v in values if isinstance(v,str) and re.fullmatch('[A-Z0-9]{10}',v))
        except ValueError:pass
    evidence={}
    for node in doc.ids('jiwu-variant-evidence'):
        try:
            candidate=json.loads(node.text())
            if isinstance(candidate,dict) and candidate.get('current_asin')==asin:evidence=candidate
        except ValueError:pass
    category=' > '.join(n.text() for n in doc.ids('wayfinding-breadcrumbs_feature_div'))
    attrs=facts['content.attributes']
    if category:attrs['category']=category[:1000]
    # An available offer does not reveal actual sellable stock quantity.
    availability=' '.join(n.text() for n in doc.ids('availability'))
    stock='0' if re.search(r'currently unavailable|out of stock',availability,re.I) else None
    from .source_images import collect_amazon_images
    lid=hashlib.sha256(f'{NAMESPACE}:{root}'.encode()).hexdigest()[:24]
    collection=collect_amazon_images(catalog,lid,asin,asin,html)
    try:urls=amazon_images(html,asin)
    except ValueError:urls=[]
    warnings=list(quote.get('issues',[]))
    if not evidence.get('complete'):
        warnings.append('仅采集已发现的子 ASIN，页面未证明变体列表完整')
    if collection['state']!='complete':warnings.append(collection['message'])
    if stock is None:warnings.append('页面未提供可核验的库存数量，请在预上架时核对填写')
    mapped={r['source_url']:r['url'] for r in collection.get('saved',[])}
    images=list(dict.fromkeys(mapped.get(url,url) for url in urls)) or collection['images']
    report=getattr(html,'wraith_test',None)
    return dict(asin=asin,source_url=url,copy=facts['content.copy'],attributes=attrs,
        images=images,price=quote.get('price'),stock=stock,
        seller=quote.get('seller') or '',quote=quote,image_collection=collection,
        variants=list(dict.fromkeys(variants))[:MAX_ROWS],variant_evidence=evidence,warnings=warnings,
        observed_at=datetime.now(timezone.utc).isoformat(),method=method,wraith_test=report)


def save_page(catalog,jid,uid,root,asin,token,data):
    with catalog.connect() as db:
        write_lock(db);job,row=target(db,jid,uid,root,asin,token)
        if row['state']=='done':return public_job(db,jid,uid)
        if row['state'] not in ('running','fallback_queued','fallback_running') or row['lease_until']<time.time():
            raise HTTPException(409,'采集任务已暂停或过期，请继续任务后重试')
        # Follow only verified variation links/maps. Child pages can reveal extra
        # combinations absent on the first page; explicit family conflicts stop.
        if root!=asin:
            parent=db.execute('SELECT data_json FROM amazon_import_items WHERE job_id=? AND root_asin=? AND asin=?',(jid,root,root)).fetchone()
            initial=json.loads(parent[0]).get('variant_evidence',{}).get('parent_asins',[]) if parent else []
            current=data.get('variant_evidence',{}).get('parent_asins',[])
            if initial and current and set(initial).isdisjoint(current):
                raise HTTPException(422,'子商品返回的父 ASIN 与当前商品组不一致，未合并入库')
        if job['include_variants']:
            count=db.execute('SELECT COUNT(*) FROM amazon_import_items WHERE job_id=?',(jid,)).fetchone()[0]
            for child in data['variants']:
                if child==asin:continue
                if count>=MAX_ROWS:
                    data['warnings'].append('本批最多采集 500 个 ASIN，更多变体请另建任务');break
                # A pasted child URL already pending as a separate root is part of
                # this confirmed family. Rehome it instead of collecting it twice.
                pending=db.execute('SELECT state FROM amazon_import_items WHERE job_id=? AND root_asin=? AND asin=?',(jid,child,child)).fetchone()
                if pending and pending['state']=='pending':
                    db.execute('DELETE FROM amazon_import_items WHERE job_id=? AND root_asin=? AND asin=?',(jid,child,child))
                    count-=1
                elsewhere=db.execute('SELECT root_asin FROM amazon_import_items WHERE job_id=? AND asin=? AND root_asin<>?',(jid,child,root)).fetchone()
                if elsewhere:
                    data['warnings'].append('子 ASIN '+child+' 已由本批另一组采集，未重复添加')
                    continue
                inserted=db.execute('INSERT INTO amazon_import_items VALUES(?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                    (jid,root,child,'pending','',0,'{}','等待采集子变体')).rowcount
                count+=inserted
        db.execute('UPDATE amazon_import_items SET state=?,data_json=?,message=? WHERE job_id=? AND root_asin=? AND asin=?',
            ('done',json.dumps(data,ensure_ascii=False),'；'.join(data['warnings'])[:1000] or '采集完成，等待入库',jid,root,asin))
        return public_job(db,jid,uid)


def run_fallback(catalog):
    """Claim exactly one persisted fallback, sharing the existing process/profile lock."""
    from .amazon_browser import fetch_page, configured
    if not configured():return False
    with catalog.connect() as db:
        write_lock(db)
        if db.execute("SELECT 1 FROM amazon_import_items WHERE state='fallback_running' AND lease_until>? LIMIT 1",(int(time.time()),)).fetchone():return False
        row=db.execute('''SELECT i.*,j.actor_id FROM amazon_import_items i JOIN amazon_import_jobs j ON j.id=i.job_id
            WHERE i.state='fallback_queued' AND j.state='collecting' AND i.lease_until>? ORDER BY j.created_at,i.asin LIMIT 1''',(int(time.time()),)).fetchone()
        if not row:return False
        row=dict(row)
        if row['actor_id']:
            user=db.execute('SELECT active,role FROM users WHERE id=?',(row['actor_id'],)).fetchone()
            if not user or not user['active'] or user['role'] not in ('admin','editor'):
                db.execute("UPDATE amazon_import_jobs SET state='paused' WHERE id=?",(row['job_id'],));return True
        db.execute("UPDATE amazon_import_items SET state='fallback_running',lease_until=?,message=? WHERE job_id=? AND root_asin=? AND asin=?",
            (int(time.time())+900,'服务器 Wraith 处理中（最多 3 次验证尝试）',row['job_id'],row['root_asin'],row['asin']))
    try:
        html=fetch_page(row['asin'])
        data=verified_page(catalog,row['root_asin'],row['asin'],html,f'https://www.amazon.com/dp/{row["asin"]}', 'wraith_background')
        save_page(catalog,row['job_id'],row['actor_id'],row['root_asin'],row['asin'],row['token'],data)
    except Exception as exc:
        message=exc.detail if isinstance(exc,HTTPException) else str(exc) if isinstance(exc,ValueError) else '服务器 Wraith 采集失败'
        with catalog.connect() as db:
            write_lock(db)
            db.execute("UPDATE amazon_import_items SET state='running',message=? WHERE job_id=? AND root_asin=? AND asin=? AND token=? AND state='fallback_running'",
                ('Wraith 未完成：'+message[:700]+'；请在员工浏览器人工处理',row['job_id'],row['root_asin'],row['asin'],row['token']))
    return True


async def fallback_worker(catalog):
    from .concurrency import background_call
    while True:
        try:await background_call(run_fallback,catalog)
        except asyncio.CancelledError:raise
        except Exception:pass
        await asyncio.sleep(2)


class NewJob(BaseModel):
    text: str=Field(min_length=1,max_length=24000)
    include_variants: bool=True
    request_id: str=Field(pattern=r'^[a-f0-9-]{32,36}$')


class Task(BaseModel):
    model_config=ConfigDict(extra='forbid')
    root_asin: str=Field(pattern=r'^[A-Z0-9]{10}$')
    asin: str=Field(pattern=r'^[A-Z0-9]{10}$')
    token: str=Field(pattern=r'^[a-f0-9]{32}$')


class Page(Task):
    html: str=Field(min_length=1,max_length=6*1024*1024)
    source_url: str=Field(max_length=2000)


class Failed(Task):
    message: str=Field(max_length=1000)


def routes(catalog):
    router=APIRouter(prefix='/api/integrations/amazon-collector')

    @router.get('')
    def status(request:Request):
        from .amazon_browser import configured
        with catalog.connect() as db:
            jobs=[public_job(db,r[0],actor(request)) for r in db.execute(
                'SELECT id FROM amazon_import_jobs WHERE actor_id=? ORDER BY created_at DESC LIMIT 20',(actor(request),))]
        return dict(jobs=jobs,wraith_configured=configured())

    @router.post('/spreadsheet')
    async def spreadsheet(request:Request, filename:str='links.xlsx'):
        from .amazon_import_sheet import parse_sheet, MAX_FILE
        from .concurrency import background_call
        # Stream to disk: a 150 MB workbook must not become two full body copies.
        with tempfile.TemporaryFile() as upload:
            size=0
            async for chunk in request.stream():
                size+=len(chunk)
                if size>MAX_FILE:raise HTTPException(413,'采集表最大 150 MB')
                await background_call(upload.write,chunk)
            upload.seek(0)
            return await background_call(parse_sheet,upload,filename)

    @router.post('/jobs')
    def create(data:NewJob,request:Request):
        asins=parse_inputs(data.text);uid=actor(request);jid=data.request_id
        signature=hashlib.sha256(json.dumps([asins,data.include_variants]).encode()).hexdigest()
        with catalog.connect() as db:
            write_lock(db)
            if db.execute('SELECT 1 FROM amazon_import_jobs WHERE id=?',(jid,)).fetchone():
                job=owned(db,jid,uid)
                if json.loads(job['result_json']).get('_request_digest')!=signature:
                    raise HTTPException(409,'该请求已创建任务且输入不同，请刷新任务列表核对')
                return public_job(db,jid,uid)
            if db.execute("SELECT COUNT(*) FROM amazon_import_jobs WHERE actor_id=? AND state IN ('collecting','paused')",(uid,)).fetchone()[0]>=5:
                raise HTTPException(429,'每人最多保留 5 个未完成采集任务，请先完成或结束旧任务')
            db.execute('INSERT INTO amazon_import_jobs VALUES(?,?,?,?,?,?)',
                (jid,uid,datetime.now(timezone.utc).isoformat(),int(data.include_variants),'collecting',json.dumps({'_request_digest':signature})))
            # Re-entering a known child refreshes the same product family.
            roots=[]
            for asin in asins:
                matches=db.execute('SELECT DISTINCT l.external_id FROM listings l JOIN variants v ON v.listing_id=l.id WHERE l.namespace=? AND v.asin=?',(NAMESPACE,asin)).fetchall()
                if len(matches)>1:raise HTTPException(409,'该 ASIN 已关联多个采集商品，请先核对商品档案：'+asin)
                root=matches[0][0] if matches else asin
                if root not in roots:roots.append(root)
            for asin in roots:db.execute('INSERT INTO amazon_import_items VALUES(?,?,?,?,?,?,?,?)',
                (jid,asin,asin,'pending','',0,'{}','等待插件采集'))
            return public_job(db,jid,uid)

    @router.get('/jobs/{jid}')
    def get(jid:str,request:Request):
        with catalog.connect() as db:return public_job(db,jid,actor(request))

    @router.post('/jobs/{jid}/claim')
    def claim(jid:str,request:Request):
        uid=actor(request);now=int(time.time())
        with catalog.connect() as db:
            write_lock(db);job=owned(db,jid,uid)
            if job['state'] in ('complete','cancelled'):return dict(task=None,job=public_job(db,jid,uid))
            db.execute("UPDATE amazon_import_items SET state='pending',token='',message='上次采集中断，等待继续' WHERE job_id=? AND state IN ('running','fallback_queued','fallback_running') AND lease_until<?",(jid,now))
            active=db.execute("SELECT 1 FROM amazon_import_items i JOIN amazon_import_jobs j ON j.id=i.job_id WHERE j.actor_id=? AND i.state IN ('running','fallback_queued','fallback_running') AND i.lease_until>?",(uid,now)).fetchone()
            if active:raise HTTPException(409,'本账号已有浏览器正在采集，请等待完成或先暂停该任务')
            db.execute("UPDATE amazon_import_jobs SET state='collecting' WHERE id=?",(jid,))
            row=db.execute("SELECT * FROM amazon_import_items WHERE job_id=? AND state='pending' ORDER BY root_asin,CASE WHEN root_asin=asin THEN 0 ELSE 1 END,asin LIMIT 1",(jid,)).fetchone()
            task=None
            if row:
                token=uuid.uuid4().hex
                db.execute("UPDATE amazon_import_items SET state='running',token=?,lease_until=?,message='员工浏览器采集中' WHERE job_id=? AND root_asin=? AND asin=?",(token,now+900,jid,row['root_asin'],row['asin']))
                task=dict(root_asin=row['root_asin'],asin=row['asin'],token=token)
            return dict(task=task,job=public_job(db,jid,uid))

    @router.post('/jobs/{jid}/page')
    def page(jid:str,data:Page,request:Request):
        uid=actor(request)
        with catalog.connect() as db:
            _,row=target(db,jid,uid,data.root_asin,data.asin,data.token)
            if row['state']=='done':return public_job(db,jid,uid)
        parsed=verified_page(catalog,data.root_asin,data.asin,data.html,data.source_url,'browser')
        return save_page(catalog,jid,uid,data.root_asin,data.asin,data.token,parsed)

    @router.post('/jobs/{jid}/fallback')
    def fallback(jid:str,data:Task,request:Request):
        from .amazon_browser import configured
        if not configured():raise HTTPException(409,'服务器尚未启用 Wraith，请在员工浏览器完成人工验证')
        with catalog.connect() as db:
            write_lock(db);_,row=target(db,jid,actor(request),data.root_asin,data.asin,data.token)
            if row['state']=='running':
                queued=db.execute("SELECT COUNT(*) FROM amazon_import_items WHERE state IN ('fallback_queued','fallback_running')").fetchone()[0]
                if queued>=20:raise HTTPException(429,'Wraith 等待队列已满，请在员工浏览器处理')
                db.execute("UPDATE amazon_import_items SET state='fallback_queued',message='等待服务器 Wraith（单通道）' WHERE job_id=? AND root_asin=? AND asin=?",(jid,data.root_asin,data.asin))
            return public_job(db,jid,actor(request))

    @router.post('/jobs/{jid}/fail')
    def fail(jid:str,data:Failed,request:Request):
        with catalog.connect() as db:
            write_lock(db);_,row=target(db,jid,actor(request),data.root_asin,data.asin,data.token)
            if row['state']!='done':db.execute("UPDATE amazon_import_items SET state='failed',message=? WHERE job_id=? AND root_asin=? AND asin=?",(data.message,jid,data.root_asin,data.asin))
            return public_job(db,jid,actor(request))

    @router.post('/jobs/{jid}/pause')
    def pause(jid:str,request:Request):
        with catalog.connect() as db:
            write_lock(db);job=owned(db,jid,actor(request))
            if job['state']=='collecting':
                db.execute("UPDATE amazon_import_jobs SET state='paused' WHERE id=?",(jid,))
                db.execute("UPDATE amazon_import_items SET state='pending',token='',lease_until=0,message='已暂停，等待继续' WHERE job_id=? AND state IN ('running','fallback_queued','fallback_running')",(jid,))
            return public_job(db,jid,actor(request))

    @router.post('/jobs/{jid}/finish')
    def finish(jid:str,request:Request):
        return finalize(catalog,jid,actor(request))

    return router


def finalize(catalog,jid,uid):
    with catalog.connect() as db:
        write_lock(db);active_actor(db,uid);job=owned(db,jid,uid)
        if job['state']=='complete':return public_job(db,jid,uid)
        rows=[dict(r) for r in db.execute('SELECT * FROM amazon_import_items WHERE job_id=?',(jid,))]
        if any(r['state'] in ('running','fallback_queued','fallback_running') for r in rows):
            raise HTTPException(409,'请先暂停正在采集的任务，再保存已采集商品')
        listings=[];captures=[];failures=[];stale=[]
        for root in dict.fromkeys(r['root_asin'] for r in rows):
            group=[r for r in rows if r['root_asin']==root]
            main=next(r for r in group if r['asin']==root)
            if main['state']!='done':failures.append(root);continue
            main_data=json.loads(main['data_json']);lid=hashlib.sha256(f'{NAMESPACE}:{root}'.encode()).hexdigest()[:24]
            old=db.execute('SELECT data_json FROM listings WHERE id=?',(lid,)).fetchone()
            old_data=json.loads(old[0]) if old else {}
            variants={v['sku']:v for v in old_data.get('variants',[])}
            completed=[json.loads(r['data_json']) for r in group if r['state']=='done']
            earliest=min(value['observed_at'] for value in completed)
            newer=db.execute('SELECT 1 FROM source_captures WHERE listing_id=? AND captured_at>? LIMIT 1',(lid,earliest)).fetchone()
            if old_data.get('observed_at','')>earliest or newer:
                stale.append(root)
                continue
            for value in completed:
                quote=value['quote']
                variant=Variant(sku=value['asin'],asin=value['asin'],source_url=value['source_url'],
                    copy=value['copy'],attributes=value['attributes'],images=value['images'],price=value['price'],stock=value['stock'],
                    seller=value['seller'],source_facts={k:quote.get(k) for k in ('price','original_price','basis_price','shipping','currency')},
                    price_check={'state':quote['status']})
                previous=variants.get(variant.sku)
                if previous and previous.get('source_facts',{}).get('collected_at','')>value['observed_at']:continue
                variant.source_facts['collected_at']=value['observed_at']
                variant.source_facts['variant_evidence']=value.get('variant_evidence',{})
                variants[variant.sku]=variant.model_dump()
                captures.append((lid,variant.sku,value))
            incomplete=[r['asin'] for r in group if r['state']!='done']
            listing=Listing(external_id=root,asin=root,sku=root,source='Amazon',source_url=main_data['source_url'],
                store='Amazon US 直接采集',template='自有插件采集',observed_at=max(v['observed_at'] for v in completed),
                evidence_kind='source_only',copy=main_data['copy'],fields=main_data['attributes'],images=main_data['images'],
                price=main_data['price'],stock=main_data['stock'],seller=main_data['seller'],
                variants=list(variants.values()),source_facts={**{k:main_data['quote'].get(k) for k in ('price','original_price','basis_price','shipping','currency')},'collector':{'job_id':jid,'root_asin':root,'incomplete_asins':incomplete,'variant_evidence':main_data.get('variant_evidence',{})}},
                issues=[{'message':'部分子变体尚未采集：'+', '.join(incomplete)}] if incomplete else [])
            listings.append(listing)
        result={'created':0,'updated':0,'unchanged':0}
        if listings:
            result=catalog.ingest(ImportBundle(namespace=NAMESPACE,label='Amazon 自有插件采集',listings=listings),uid or None,connection=db)
            for lid,sku,value in captures:
                save_capture(catalog,db,lid,sku,value['asin'],value['quote']|{'listing_facts':{'content.copy':value['copy'],'content.attributes':value['attributes']},
                    'image_collection':value['image_collection'],'wraith_test':value['wraith_test']},uid or None,value['source_url'],value['method'],observed_at=value['observed_at'],sync_to_catalog=False)
        result['_request_digest']=json.loads(job['result_json']).get('_request_digest')
        result.update(listing_ids=[hashlib.sha256(f'{NAMESPACE}:{l.external_id}'.encode()).hexdigest()[:24] for l in listings],failed_roots=failures,stale_roots=stale,
                      incomplete=sum(r['state']!='done' for r in rows))
        db.execute("UPDATE amazon_import_jobs SET state='complete',result_json=? WHERE id=?",(json.dumps(result),jid))
        return public_job(db,jid,uid)
