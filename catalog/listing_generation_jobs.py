"""Durable preparation jobs. Never call submit/stock APIs or hold a DB lock during AI."""
import copy
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, Request

from .database import write_lock
from .credential_vault import decrypt
from .listing_ai_client import ListingAIClient, ListingAISettings, ListingAIError
from .listing_generation import (RULES_VERSION, source_facts, choose_type, fill_row,
                                 validate_generated)
from .prelisting import encoded, fingerprint
from .walmart_accounts import now
from .walmart_listing_schema import DEFAULT_VERSION, normalize_spec, taxonomy_options
from .walmart_publishing import (batch, binding, document, public_document, fresh_basis,
    default_rows, latest_results, EDITABLE, cache_value, cache_put, unchanged_sources, source_issues)

POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='listing-preparation')


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS listing_generation_jobs(
        id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES prelisting_batches(id),
        state TEXT NOT NULL,revision INTEGER NOT NULL,store_id INTEGER NOT NULL,partner_id TEXT NOT NULL,
        binding_revision INTEGER NOT NULL,input_hash TEXT NOT NULL,actor_id INTEGER,
        report_json TEXT NOT NULL,message TEXT NOT NULL,started_at BIGINT NOT NULL,updated_at BIGINT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS generation_batch ON listing_generation_jobs(batch_id,started_at)')
    db.execute('INSERT INTO migrations VALUES(17) ON CONFLICT DO NOTHING')


def public_job(row):
    if not row:
        return None
    return {k: row[k] for k in ('id','batch_id','state','revision','message','started_at','updated_at')} | {
        'report':json.loads(row['report_json'])}


def latest(db, bid):
    row = db.execute('SELECT * FROM listing_generation_jobs WHERE batch_id=? ORDER BY started_at DESC,id DESC LIMIT 1', (bid,)).fetchone()
    if row and row['state'] in ('queued','running') and row['updated_at'] < time.time()-600:
        db.execute("UPDATE listing_generation_jobs SET state='interrupted',message=? WHERE id=? AND state IN ('queued','running')",
                   ('生成任务已中断，已保存的商品资料保留，请重新生成未完成项', row['id']))
        row = db.execute('SELECT * FROM listing_generation_jobs WHERE id=?', (row['id'],)).fetchone()
    return row


def start_job(catalog, publisher, bid, actor_id=None, ai=None, schedule=True):
    ai = ai or ListingAIClient()
    with catalog.connect() as db:
        write_lock(db)
        current = batch(db,bid)
        prior = latest(db,bid)
        if prior and prior['state'] in ('queued','running'):
            return public_job(prior)
        doc = public_document(db,bid)
        if not doc:
            basis = fresh_basis(catalog,db,current)
            db.execute('INSERT INTO walmart_listing_documents VALUES(?,?,?,?,?,?,?)',
                (bid,1,os.environ.get('CATALOG_WALMART_SPEC_VERSION',DEFAULT_VERSION),encoded(default_rows(basis)),encoded(basis),'{}',now()))
        stored = document(db,bid)
        basis = unchanged_sources(db,stored)
        _,bound = binding(db,basis['store']['id'])
        schemas = {r['product_type']: cache_value(db,bound,'spec',stored['version'],r['product_type'])
                   for r in json.loads(stored['rows_json']) if r['product_type']}
        schema_digests = {pt:fingerprint(s) for pt,s in schemas.items() if s}
        identity = fingerprint(dict(rows=json.loads(stored['rows_json']),basis=basis,
            version=stored['version'],rules=RULES_VERSION,model=ai.settings.model,
            schemas=schema_digests,partner=bound['partner_id'],binding_revision=bound['revision']))
        if (prior and prior['state']=='complete' and prior['input_hash']==identity
                and all(r['state'] in ('complete','skipped') for r in json.loads(prior['report_json'])['rows'].values())):
            return public_job(prior)
        previous_fields = {}
        if prior:
            previous_fields = {sku:r.get('fields',{}) for sku,r in json.loads(prior['report_json']).get('rows',{}).items()}
        jid, timestamp = uuid.uuid4().hex, int(time.time())
        started = max(int(time.time()*1000), prior['started_at']+1 if prior else 0)
        db.execute('INSERT INTO listing_generation_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (jid,bid,'queued',stored['revision'],basis['store']['id'],bound['partner_id'],bound['revision'],identity,
             actor_id,encoded({'rules_version':RULES_VERSION,'model':ai.settings.model,'rows':{},'previous_fields':previous_fields}),
             '准备读取商品来源与官方类目',started,timestamp))
        row = db.execute('SELECT * FROM listing_generation_jobs WHERE id=?',(jid,)).fetchone()
    if schedule:
        POOL.submit(run_job,catalog,publisher,jid,ai)
    return public_job(row)


def auto_start(catalog, publisher, bid, actor_id=None):
    try:
        if ListingAISettings().configured:
            start_job(catalog,publisher,bid,actor_id)
    except (ValueError, HTTPException):
        # Unbound stores remain valid draft batches. The editor explains the prerequisite.
        return


def checked_context(db, job):
    active = db.execute('SELECT state FROM listing_generation_jobs WHERE id=?', (job['id'],)).fetchone()
    if not active or active['state'] not in ('queued','running'):
        raise ListingAIError('生成任务已停止，旧任务不会继续覆盖资料')
    batch(db,job['batch_id'])
    _,bound = binding(db,job['store_id'],job['partner_id'])
    if bound['revision'] != job['binding_revision']:
        raise ListingAIError('店铺绑定已改变，已停止生成')
    stored = document(db,job['batch_id'])
    if stored['revision'] != job['revision']:
        raise ListingAIError('资料已被其他操作修改，已停止覆盖；请重新生成缺项')
    unchanged_sources(db,stored)
    return stored, bound


def run_job(catalog, publisher, jid, ai):
    report = {}
    try:
        with catalog.connect() as db:
            write_lock(db)
            row = db.execute('SELECT * FROM listing_generation_jobs WHERE id=?',(jid,)).fetchone()
            if not row or row['state'] != 'queued':
                return
            job = dict(row); report = json.loads(job['report_json'])
            stored, bound = checked_context(db,job)
            basis = json.loads(stored['basis_json'])
            rows = json.loads(stored['rows_json'])
            report['total'] = len(rows)
            db.execute("UPDATE listing_generation_jobs SET state='running',updated_at=? WHERE id=?",(int(time.time()),jid))
            sources = {i['listing_id']:json.loads(db.execute('SELECT data_json FROM current_listings WHERE id=?', (i['listing_id'],)).fetchone()[0]) for i in basis['items']}
            types = cache_value(db,bound,'taxonomy',age=3600)
            nodes = cache_value(db,bound,'nodes',age=3600)
        creds = decrypt(bound['store_id'],bound['encrypted_credentials'])
        with publisher.session(creds,bound['partner_id']) as remote:
            if types is None:
                types = taxonomy_options(remote.taxonomy())
            if nodes is None:
                raw_nodes = remote.nodes()
                # Reuse the exact store-node normalization from the publishing API.
                from .walmart_publishing import normalize_nodes
                nodes = normalize_nodes(raw_nodes)
            with catalog.connect() as db:
                write_lock(db);checked_context(db,job)
                cache_put(db,bound,'taxonomy',types)
                cache_put(db,bound,'nodes',nodes)
            by_sku = {v['platform_sku']:(i,v) for i in basis['items'] for v in i['variants']}
            for original in rows:
                sku = original['platform_sku']
                with catalog.connect() as db:
                    write_lock(db);stored,_ = checked_context(db,job)
                    status = latest_results(db,job['batch_id']).get(sku,{}).get('status','unsubmitted')
                    db.execute('UPDATE listing_generation_jobs SET updated_at=?,message=? WHERE id=?',
                        (int(time.time()),f'正在生成 {sku}',jid))
                if status not in EDITABLE:
                    report['rows'][sku] = dict(state='skipped',issues=[dict(path='',message='已有提交结果，保留并跳过')],fields={})
                    continue
                item, variant = by_sku[sku]
                updated = copy.deepcopy(original)
                provenance, issues = {}, []
                for path, evidence in report.get('previous_fields',{}).get(sku,{}).items():
                    parts = path.split('.',1)
                    current_value = original.get(parts[0]) if len(parts)==1 else original.get(parts[0],{}).get(parts[1])
                    if evidence.get('value') == current_value:
                        provenance[path] = evidence
                try:
                    facts = source_facts(sources[item['listing_id']],item,variant)
                    pt, decision = choose_type(updated,item,facts,types,ai)
                    updated['product_type'] = pt
                    if decision['kind'] != 'existing' or 'product_type' not in provenance:
                        provenance['product_type'] = decision | {'value':pt}
                    if not updated['fulfillment_center_id'] and len(nodes)==1:
                        updated['fulfillment_center_id'] = nodes[0]['id']
                        provenance['fulfillment_center_id'] = dict(kind='user_rule',value=nodes[0]['id'],reason='绑定店铺唯一启用发货仓',refs=[])
                    with catalog.connect() as db:
                        schema = cache_value(db,bound,'spec',stored['version'],pt)
                    if schema is None:
                        schema = normalize_spec(remote.spec(pt,stored['version']),pt,stored['version'])
                        with catalog.connect() as db:
                            write_lock(db);checked_context(db,job)
                            cache_put(db,bound,'spec',schema,stored['version'],pt)
                    updated, issues = fill_row(updated,schema,facts,ai,provenance)
                    issues += validate_generated(updated,schema,stored['version'],os.environ.get('CATALOG_PUBLIC_ORIGIN',''))
                    issues += [dict(path='',message=m) for m in source_issues(item,variant)]
                except (ListingAIError, ValueError) as exc:
                    # Only known safe exception strings. Provider bodies are never persisted.
                    message = str(exc) if isinstance(exc,ListingAIError) else '该商品生成失败，请重试或检查官方规格'
                    issues.append(dict(path='product_type' if not updated['product_type'] else '',message=message))
                # Preserve all identity/pricing values even if a future generator changes.
                for key in ('platform_sku','listing_id','source_sku','identification'):
                    if updated[key] != original[key]:
                        raise ListingAIError('生成结果改变商品身份，已停止保存')
                for key in ('price','msrp','inventory','sku','productIdentifiers'):
                    if updated['orderable'].get(key) != original['orderable'].get(key):
                        raise ListingAIError('生成结果改变价格或库存，已停止保存')
                report['rows'][sku] = dict(state='review' if issues else 'complete',
                    issues=list({(i['path'],i['message']):i for i in issues}.values()), fields=provenance,
                    estimated=sum(v.get('kind')=='estimate' for v in provenance.values()))
                with catalog.connect() as db:
                    write_lock(db);stored,_ = checked_context(db,job)
                    if latest_results(db,job['batch_id']).get(sku,{}).get('status','unsubmitted') not in EDITABLE:
                        raise ListingAIError('商品已开始提交，停止写入生成资料')
                    live_rows = json.loads(stored['rows_json'])
                    for n,r in enumerate(live_rows):
                        if r['platform_sku']==sku:
                            live_rows[n] = updated
                    changed = live_rows != json.loads(stored['rows_json'])
                    if changed:
                        db.execute('UPDATE walmart_listing_documents SET revision=revision+1,rows_json=?,validation_json=?,updated_at=? WHERE batch_id=?',
                            (encoded(live_rows),'{}',now(),job['batch_id']))
                        job['revision'] += 1
                    db.execute('UPDATE listing_generation_jobs SET revision=?,report_json=?,updated_at=? WHERE id=?',
                        (job['revision'],encoded(report),int(time.time()),jid))
                    reread = json.loads(document(db,job['batch_id'])['rows_json'])
                    if next(r for r in reread if r['platform_sku']==sku) != updated:
                        raise ListingAIError('生成资料保存回读不一致')
        with catalog.connect() as db:
            write_lock(db);stored,_ = checked_context(db,job)
            schemas = {r['product_type']: cache_value(db,bound,'spec',stored['version'],r['product_type'])
                       for r in json.loads(stored['rows_json']) if r['product_type']}
            updated_identity = fingerprint(dict(rows=json.loads(stored['rows_json']),basis=json.loads(stored['basis_json']),
                version=stored['version'],rules=RULES_VERSION,model=ai.settings.model,
                schemas={pt:fingerprint(s) for pt,s in schemas.items() if s},
                partner=bound['partner_id'],binding_revision=bound['revision']))
            report.pop('previous_fields',None)
            complete = sum(r['state']=='complete' for r in report['rows'].values())
            db.execute("UPDATE listing_generation_jobs SET state='complete',input_hash=?,report_json=?,message=?,updated_at=? WHERE id=?",
                (updated_identity,encoded(report),f'资料生成结束：{complete} 个 SKU 完整，其余请查看具体缺项',int(time.time()),jid))
            db.execute('INSERT INTO walmart_listing_events VALUES(?,?,?,?,?,?)',
                (uuid.uuid4().hex,job['batch_id'],job['actor_id'],'generate',now(),encoded({'job_id':jid,'rules':RULES_VERSION})))
    except Exception as exc:
        message = str(exc) if isinstance(exc,ListingAIError) else '生成任务中断：店铺、来源、规格或连接异常；已保存资料保留，可重试未完成项'
        with catalog.connect() as db:
            write_lock(db)
            db.execute("UPDATE listing_generation_jobs SET state='failed',message=?,report_json=?,updated_at=? WHERE id=? AND state IN ('queued','running')",
                (message,encoded(report),int(time.time()),jid))


def generation_routes(catalog, publisher):
    router = APIRouter(prefix='/api/prelisting/batches/{bid}/walmart/generation')

    @router.get('')
    def read(bid: str):
        with catalog.connect() as db:
            write_lock(db);batch(db,bid)
            return dict(job=public_job(latest(db,bid)),ai=ListingAISettings().public())

    @router.post('')
    def start(bid: str,request: Request):
        # Re-read app.state so tests and provider adapters can inject their client.
        return start_job(catalog,request.app.state.walmart_publisher,bid,
                         getattr(request.state,'user',{}).get('id'))
    return router
