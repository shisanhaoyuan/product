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
                                 validate_generated, summarize_generation_issues)
from .prelisting import encoded, fingerprint
from .walmart_accounts import now
from datetime import datetime,timezone
from .listing_prerequisites import resource_digest, apply_prerequisites, validation_current
from .listing_repair import repair_row, repair_fields, MAX_REPAIR_ROUNDS, prerequisite_issue
from .listing_source_evidence import supplement_facts
from .listing_vision import image_evidence, vision_identity
from .walmart_listing_schema import DEFAULT_VERSION, normalize_spec, taxonomy_options
from .walmart_publishing import (batch, binding, document, public_document, fresh_basis,
    default_rows, normalize_titles, normalize_document_titles, latest_results, EDITABLE, cache_value, cache_put, unchanged_sources, source_issues, check_rows, validate_document)

POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='listing-preparation')


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS listing_generation_jobs(
        id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES prelisting_batches(id),
        state TEXT NOT NULL,revision INTEGER NOT NULL,store_id INTEGER NOT NULL,partner_id TEXT NOT NULL,
        binding_revision INTEGER NOT NULL,input_hash TEXT NOT NULL,actor_id INTEGER,
        report_json TEXT NOT NULL,message TEXT NOT NULL,started_at BIGINT NOT NULL,updated_at BIGINT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS generation_batch ON listing_generation_jobs(batch_id,started_at)')
    db.execute('INSERT INTO migrations VALUES(17) ON CONFLICT DO NOTHING')
    from .listing_prerequisites import initialize as initialize_preparation
    initialize_preparation(db)


def public_job(row, db=None):
    if not row:
        return None
    report = json.loads(row['report_json'])
    if db is not None:
        stored=document(db,row['batch_id'])
        bound=db.execute('SELECT * FROM walmart_accounts WHERE store_id=?',(row['store_id'],)).fetchone()
        current=validation_current(db,stored,bound,json.loads(stored['validation_json']))
        report['readiness_current']=current
        if not current:
            for entry in report.get('rows',{}).values():
                if entry.get('preparation_state')=='ready':
                    entry['preparation_state']='needs_validation'
    for result in report.get('rows', {}).values():
        result['issues'] = summarize_generation_issues(result.get('issues', []))
    return {k: row[k] for k in ('id','batch_id','state','revision','message','started_at','updated_at')} | {
        'report':report}


def latest(db, bid):
    row = db.execute('SELECT * FROM listing_generation_jobs WHERE batch_id=? ORDER BY started_at DESC,id DESC LIMIT 1', (bid,)).fetchone()
    if row and row['state'] in ('queued','running','awaiting_sources') and row['updated_at'] < time.time()-(3600 if row['state']=='awaiting_sources' else 600):
        db.execute("UPDATE listing_generation_jobs SET state='interrupted',message=? WHERE id=? AND state IN ('queued','running','awaiting_sources')",
                   ('生成任务已中断，已保存的商品资料保留，请重新生成未完成项', row['id']))
        row = db.execute('SELECT * FROM listing_generation_jobs WHERE id=?', (row['id'],)).fetchone()
    return row


def start_job(catalog, publisher, bid, actor_id=None, ai=None, schedule=True, source_mode='shared'):
    ai = ai or ListingAIClient()
    with catalog.connect() as db:
        write_lock(db)
        current = batch(db,bid)
        prior = latest(db,bid)
        if prior and prior['state'] in ('queued','running','awaiting_sources'):
            return public_job(prior)
        doc = public_document(db,bid)
        if not doc:
            basis = fresh_basis(catalog,db,current)
            db.execute('INSERT INTO walmart_listing_documents VALUES(?,?,?,?,?,?,?)',
                (bid,1,os.environ.get('CATALOG_WALMART_SPEC_VERSION',DEFAULT_VERSION),encoded(default_rows(basis)),encoded(basis),'{}',now()))
        from .listing_preparation_source import refresh_amazon
        refresh_amazon(catalog,db,document(db,bid),actor_id)
        stored = normalize_document_titles(db,document(db,bid))
        from .listing_prerequisites import allocate_document_identifiers
        allocate_document_identifiers(db,stored,actor_id)
        stored = document(db,bid)
        basis = unchanged_sources(db,stored)
        _,bound = binding(db,basis['store']['id'])
        schemas = {r['product_type']: cache_value(db,bound,'spec',stored['version'],r['product_type'])
                   for r in json.loads(stored['rows_json']) if r['product_type']}
        schema_digests = {pt:fingerprint(s) for pt,s in schemas.items() if s}
        identity = fingerprint(dict(rows=json.loads(stored['rows_json']),basis=basis,
            version=stored['version'],rules=RULES_VERSION,model=vision_identity(ai),feedback=latest_results(db,bid),
            features=[getattr(ai.settings,'vision',False),getattr(ai.settings,'supplement',False)],resources=resource_digest(db,bound),
            schemas=schema_digests,partner=bound['partner_id'],binding_revision=bound['revision']))
        # A new Amazon filling run must observe current quotes, even for identical inputs.
        statuses=latest_results(db,bid)
        amazon_editable=any(i.get('source')=='Amazon' and any(
            statuses.get(v['platform_sku'],{}).get('status','unsubmitted') in EDITABLE
            for v in i['variants']) for i in basis['items'])
        if (not amazon_editable and prior and prior['state']=='complete' and prior['input_hash']==identity
                and validation_current(db,stored,bound,json.loads(stored['validation_json']))
                and all(r['state'] in ('complete','skipped') for r in json.loads(prior['report_json'])['rows'].values())):
            return public_job(prior)
        previous_fields = {}
        if prior:
            previous_fields = {sku:r.get('fields',{}) for sku,r in json.loads(prior['report_json']).get('rows',{}).items()}
            # A user changing and later restoring a generated value is still a
            # manual decision. Equality alone is not proof of no manual edits.
            since=datetime.fromtimestamp(prior['started_at']/1000,timezone.utc).isoformat()
            for e in db.execute("SELECT details_json FROM walmart_listing_events WHERE batch_id=? AND action='edit' AND at>=?",(bid,since)):
                change=json.loads(e[0]);old={r['platform_sku']:r for r in change.get('before',[])}
                for current in change.get('after',[]):
                    sku=current['platform_sku']
                    for path,evidence in previous_fields.get(sku,{}).items():
                        parts=path.split('.',1)
                        before=old.get(sku,{})
                        b=before.get(parts[0]) if len(parts)==1 else before.get(parts[0],{}).get(parts[1])
                        a=current.get(parts[0]) if len(parts)==1 else current.get(parts[0],{}).get(parts[1])
                        if a!=b:
                            evidence['manual_locked']=True
        from .listing_shared_collection import source_targets
        targets=source_targets(basis,statuses) if source_mode=='shared' else []
        state='awaiting_sources' if targets else 'queued'
        if targets:
            # Earlier validation cannot authorize submission while new quotes are pending.
            db.execute('UPDATE walmart_listing_documents SET validation_json=? WHERE batch_id=?',('{}',bid))
        jid, timestamp = uuid.uuid4().hex, int(time.time())
        started = max(int(time.time()*1000), prior['started_at']+1 if prior else 0)
        db.execute('INSERT INTO listing_generation_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (jid,bid,state,stored['revision'],basis['store']['id'],bound['partner_id'],bound['revision'],identity,
             actor_id,encoded({'rules_version':RULES_VERSION,'model':ai.settings.model,'rows':{},'previous_fields':previous_fields,'resources_digest':resource_digest(db,bound),'source_mode':source_mode,'source_targets':targets,'source_digests':{i['listing_id']:i['source_digest'] for i in basis['items']}}),
             '等待商品库采集会话；邮编失效时请在打开的网页设置 10001' if targets else '准备后台采集、计算售价、AI 填充与校验',started,timestamp))
        row = db.execute('SELECT * FROM listing_generation_jobs WHERE id=?',(jid,)).fetchone()
    if schedule and state=='queued':
        POOL.submit(run_job,catalog,publisher,jid,ai)
    return public_job(row)


def auto_start(catalog, publisher, bid, actor_id=None):
    try:
        if ListingAISettings().configured:
            start_job(catalog,publisher,bid,actor_id,source_mode='shared')
    except (ValueError, HTTPException):
        # Unbound stores remain valid draft batches. The editor explains the prerequisite.
        return


def checked_context(db, job, check_sources=True):
    active = db.execute('SELECT state FROM listing_generation_jobs WHERE id=?', (job['id'],)).fetchone()
    if not active or active['state'] not in ('queued','running','awaiting_sources'):
        raise ListingAIError('生成任务已停止，旧任务不会继续覆盖资料')
    if job.get('actor_id') is not None:
        actor=db.execute('SELECT role,active,must_change_password FROM users WHERE id=?',(job['actor_id'],)).fetchone()
        if not actor or not actor['active'] or actor['must_change_password'] or actor['role'] not in ('admin','editor'):
            raise ListingAIError('生成操作者权限已失效，已停止写入；保存资料保留')
    batch(db,job['batch_id'])
    _,bound = binding(db,job['store_id'],job['partner_id'])
    if bound['revision'] != job['binding_revision']:
        raise ListingAIError('店铺绑定已改变，已停止生成')
    stored = document(db,job['batch_id'])
    if stored['revision'] != job['revision']:
        raise ListingAIError('资料已被其他操作修改，已停止覆盖；请重新生成缺项')
    if check_sources:
        unchanged_sources(db,stored)
    if job.get('resources_digest') and resource_digest(db,bound)!=job['resources_digest']:
        raise ListingAIError('生成前提配置或真实编码池已改变，已停止覆盖，请重新生成')
    return stored, bound


def run_job(catalog, publisher, jid, ai):
    report = {}
    try:
        with catalog.connect() as db:
            write_lock(db)
            row=db.execute('SELECT * FROM listing_generation_jobs WHERE id=?',(jid,)).fetchone()
            if not row or row['state']!='queued':
                return
            job=dict(row);report=json.loads(job['report_json'])
            job['resources_digest']=report.get('resources_digest')
            stored,bound=checked_context(db,job)
            basis=json.loads(stored['basis_json']);rows=json.loads(stored['rows_json'])
            report['total']=len(rows)
            db.execute("UPDATE listing_generation_jobs SET state='running',updated_at=? WHERE id=?",(int(time.time()),jid))
            sources={i['listing_id']:json.loads(db.execute('SELECT data_json FROM current_listings WHERE id=?',
                (i['listing_id'],)).fetchone()[0]) for i in basis['items']}
            types=cache_value(db,bound,'taxonomy',age=3600);nodes=cache_value(db,bound,'nodes',age=3600)

        def progress(sku,stage,message):
            entry=report['rows'].setdefault(sku,dict(state='running',preparation_state=stage,issues=[],fields={},rounds=[]))
            entry['preparation_state']=stage
            with catalog.connect() as db:
                write_lock(db);checked_context(db,job)
                db.execute('UPDATE listing_generation_jobs SET report_json=?,message=?,updated_at=? WHERE id=?',
                    (encoded(report),message,int(time.time()),jid))

        def save_row(updated, expected_identity):
            sku=updated['platform_sku']
            original=next(r for r in rows if r['platform_sku']==sku)
            for key in ('platform_sku','listing_id','source_sku'):
                if updated[key]!=original[key]:
                    raise ListingAIError('生成结果改变商品身份，已停止保存')
            if updated['identification']!=expected_identity:
                raise ListingAIError('AI 修改了真实编码或豁免绑定，已停止保存')
            for key in ('price','msrp','inventory','sku','productIdentifiers'):
                if updated['orderable'].get(key)!=original['orderable'].get(key):
                    raise ListingAIError('生成结果改变价格或库存，已停止保存')
            with catalog.connect() as db:
                write_lock(db);stored,_=checked_context(db,job)
                if latest_results(db,job['batch_id']).get(sku,{}).get('status','unsubmitted') not in EDITABLE:
                    raise ListingAIError('商品已开始提交，停止写入生成资料')
                title_changes=normalize_titles(db,[updated],basis,{},stored['version'])
                if title_changes:
                    change=title_changes[0]
                    fields=report['rows'][sku].setdefault('fields',{})
                    if change['before']!=change['after']:
                        fields['visible.productName']=dict(kind='user_rule',
                            value=change['after'],previous_value=change['before'],refs=[],reason='用户指定：目标品牌置于标题开头，移除已识别品牌')
                    fields.update(change['origin_rule'])
                live_rows=json.loads(stored['rows_json'])
                for n,value in enumerate(live_rows):
                    if value['platform_sku']==sku:
                        live_rows[n]=updated
                if live_rows!=json.loads(stored['rows_json']):
                    db.execute('UPDATE walmart_listing_documents SET revision=revision+1,rows_json=?,validation_json=?,updated_at=? WHERE batch_id=?',
                        (encoded(live_rows),'{}',now(),job['batch_id']))
                    job['revision']+=1
                db.execute('UPDATE listing_generation_jobs SET revision=?,report_json=?,updated_at=? WHERE id=?',
                    (job['revision'],encoded(report),int(time.time()),jid))
                reread=json.loads(document(db,job['batch_id'])['rows_json'])
                if next(r for r in reread if r['platform_sku']==sku)!=updated:
                    raise ListingAIError('生成资料保存回读不一致')

        from .listing_preparation_source import collect_before_generation
        stored=collect_before_generation(catalog,job,report,progress,checked_context)
        basis=json.loads(stored['basis_json']);rows=json.loads(stored['rows_json'])
        with catalog.connect() as db:
            checked_context(db,job)
            sources={i['listing_id']:json.loads(db.execute('SELECT data_json FROM current_listings WHERE id=?',
                (i['listing_id'],)).fetchone()[0]) for i in basis['items']}
        creds=decrypt(bound['store_id'],bound['encrypted_credentials'])
        with publisher.session(creds,bound['partner_id']) as remote:
            if types is None:
                types=taxonomy_options(remote.taxonomy())
            if nodes is None:
                from .walmart_publishing import normalize_nodes
                nodes=normalize_nodes(remote.nodes())
            with catalog.connect() as db:
                write_lock(db);checked_context(db,job)
                cache_put(db,bound,'taxonomy',types);cache_put(db,bound,'nodes',nodes)
            by_sku={v['platform_sku']:(i,v) for i in basis['items'] for v in i['variants']}
            for original in rows:
                sku=original['platform_sku']
                with catalog.connect() as db:
                    write_lock(db);checked_context(db,job)
                    status=latest_results(db,job['batch_id']).get(sku,{}).get('status','unsubmitted')
                if status not in EDITABLE:
                    report['rows'][sku]=dict(state='skipped',preparation_state='skipped',
                        issues=[dict(path='',message='已有提交结果，保留并跳过')],fields={},rounds=[])
                    continue
                progress(sku,'collecting',f'正在补全 {sku} 的同 SKU 来源')
                entry=report['rows'][sku];item,variant=by_sku[sku];updated=copy.deepcopy(original)
                provenance={}
                for path,evidence in report.get('previous_fields',{}).get(sku,{}).items():
                    parts=path.split('.',1)
                    value=original.get(parts[0]) if len(parts)==1 else original.get(parts[0],{}).get(parts[1])
                    if evidence.get('value')==value:
                        provenance[path]=evidence
                entry['fields']=provenance
                issues=[];blockers=[];schema=None;facts={};expected_identity=copy.deepcopy(original['identification'])
                try:
                    # Amazon quote collection is already complete and persisted above.
                    # Do not launch a second competing fetch of the same page during AI filling.
                    if item.get('source')=='Amazon' and 'source_collection' in entry:
                        collection=entry['source_collection']
                        if collection.get('facts'):
                            facts,detail_collection=supplement_facts(catalog,sources[item['listing_id']],item,variant,ai,collection['facts'])
                            if detail_collection.get('state')=='conflict':
                                collection=detail_collection
                            elif detail_collection.get('copy_differences'):
                                collection = collection | {'copy_differences': detail_collection['copy_differences'],
                                                           'message': detail_collection['message']}
                        else:
                            facts=source_facts(sources[item['listing_id']],item,variant)
                    else:
                        facts,collection=supplement_facts(catalog,sources[item['listing_id']],item,variant,ai)
                    entry['collection']=collection
                    if collection.get('state')=='conflict':
                        blockers.append(dict(path='',message=collection['message'],source_digest=item['source_digest']))
                    progress(sku,'collecting',f'正在采集并保存 {sku} 的图片')
                    from .source_images import collect_images, localize_row, recover_imported_images
                    urls=[updated['visible'].get('mainImageUrl',''),
                          *updated['visible'].get('productSecondaryImageURL',[]),
                          *variant.get('content',{}).get('images',[])]
                    image_collection=collect_images(catalog,item['listing_id'],variant['sku'],urls)
                    if 'source_collection' in entry:
                        from .source_images import reuse_verified_source_gallery
                        image_collection=reuse_verified_source_gallery(
                            image_collection,entry['source_collection'],variant.get('asin',''))
                    else:
                        # The quote stage may already have tried this ASIN; never loop on a blocked page.
                        image_collection=recover_imported_images(catalog,item,variant,urls,image_collection)
                    entry['image_collection']=image_collection
                    localize_row(updated,image_collection)
                    # Feed AI the collected bytes, not a second download of the same external URL.
                    variant=copy.deepcopy(variant)
                    if image_collection['images']:
                        variant.setdefault('content',{})['images']=image_collection['images']
                        variant['_collected_images']=image_collection['images']
                    progress(sku,'generating',f'正在生成 {sku} 刊登资料')
                    try:
                        pt,decision=choose_type(updated,item,facts,types,ai)
                    except ListingAIError as exc:
                        issues.append(dict(path='product_type',message=str(exc)))
                        blockers.append(dict(path='product_type',value=updated['product_type'],message=str(exc),source_digest=item['source_digest']))
                        raise
                    prior_type=provenance.get('product_type',{})
                    if (original['product_type'] and pt!=original['product_type'] and
                            (prior_type.get('manual_locked') or prior_type.get('kind') not in ('source','ai_source'))):
                        blockers.append(dict(path='product_type',value=original['product_type'],source_digest=item['source_digest'],
                            message='现有人工商品类型与来源不一致，请确认类目后重新生成，系统未覆盖人工选择'))
                        raise ListingAIError('现有人工商品类型与来源不一致，已保留人工选择')
                    updated['product_type']=pt
                    if decision['kind']!='existing' or 'product_type' not in provenance:
                        provenance['product_type']=decision | {'value':pt}
                    apply_prerequisites(catalog,bound,updated,item,nodes,remote,provenance,lambda db:checked_context(db,job))
                    expected_identity=copy.deepcopy(updated['identification'])
                    if not updated['fulfillment_center_id'] and len(nodes)==1:
                        updated['fulfillment_center_id']=nodes[0]['id']
                        provenance['fulfillment_center_id']=dict(kind='user_rule',value=nodes[0]['id'],refs=[],reason='绑定店铺唯一启用发货仓')
                    with catalog.connect() as db:
                        schema=cache_value(db,bound,'spec',stored['version'],pt)
                    if schema is None:
                        schema=normalize_spec(remote.spec(pt,stored['version']),pt,stored['version'])
                        with catalog.connect() as db:
                            write_lock(db);checked_context(db,job);cache_put(db,bound,'spec',schema,stored['version'],pt)
                    try:
                        if image_collection['images']:
                            visual,vision=image_evidence(catalog,updated,variant,ai,schema)
                        else:
                            visual,vision=[],dict(state='failed',images=[],message=image_collection['message'])
                        entry['vision']=vision
                        if visual:
                            facts['visual_evidence']=[v for v in visual if v.get('attribute')!='listingImageRole']
                    except ListingAIError as exc:
                        entry['vision']=dict(state='failed',message=str(exc),images=[])
                    updated,issues=fill_row(updated,schema,facts,ai,provenance)
                except (ListingAIError,ValueError) as exc:
                    if not facts:
                        blockers.append(dict(path='',message='同 SKU 来源无法核实，请处理来源后重新生成',source_digest=item['source_digest']))
                    if not issues:
                        issues=[dict(path='',message=str(exc) if isinstance(exc,ListingAIError) else '资料生成失败，请检查来源和官方规格')]
                entry['issues']=issues
                entry['generation_issues']=copy.deepcopy(issues)
                with catalog.connect() as db:
                    write_lock(db);checked_context(db,job)
                    db.execute('''INSERT INTO listing_preparation_blockers VALUES(?,?,?) ON CONFLICT(batch_id,platform_sku)
                        DO UPDATE SET data_json=excluded.data_json''',(job['batch_id'],sku,encoded(blockers)))
                save_row(updated,expected_identity)
                seen=set()
                for round_number in range(MAX_REPAIR_ROUNDS+1):
                    progress(sku,'validating',f'正在校验 {sku} 的上架资料')
                    with catalog.connect() as db:
                        snapshot,_=checked_context(db,job)
                        validation=check_rows(db,snapshot,remote,bound,skus={sku})
                    current_errors=validation['rows'][sku]['errors']
                    # Collection conflicts and blocked prerequisites cannot be
                    # hidden by a formally valid set of ordinary attributes.
                    entry['issues']=current_errors
                    if not current_errors or schema is None or round_number==MAX_REPAIR_ROUNDS:
                        break
                    digest=fingerprint(dict(row=updated,errors=summarize_generation_issues(current_errors),facts=facts))
                    if digest in seen or not repair_fields(updated,schema,current_errors,provenance):
                        break
                    seen.add(digest)
                    progress(sku,'repairing',f'正在修复 {sku} 第{round_number+1}轮')
                    with catalog.connect() as db:
                        write_lock(db);checked_context(db,job)
                        db.execute('INSERT INTO listing_preparation_rounds VALUES(?,?,?,?,?,?,?)',
                            (jid,sku,round_number+1,digest,'started',encoded({'errors':current_errors}),int(time.time())))
                    try:
                        candidate,repair=repair_row(updated,schema,current_errors,facts,ai,provenance,round_number+1)
                    except ListingAIError as exc:
                        repair=dict(state='failed',message=str(exc),issues=current_errors);candidate=updated
                    with catalog.connect() as db:
                        write_lock(db);checked_context(db,job)
                        db.execute('UPDATE listing_preparation_rounds SET state=?,data_json=?,updated_at=? WHERE job_id=? AND platform_sku=? AND round=?',
                            (repair['state'],encoded(repair),int(time.time()),jid,sku,round_number+1))
                    entry['rounds'].append(repair)
                    if candidate==updated:
                        break
                    updated=candidate;save_row(updated,expected_identity)
                entry['estimated']=sum(v.get('kind') in ('estimate','visual_inference') for v in provenance.values())
                entry['state']='review' if entry['issues'] else 'complete'
                entry['preparation_state']=('needs_prerequisite' if any(prerequisite_issue(i) for i in entry['issues']) else
                    'needs_review') if entry['issues'] else 'validating'
                save_row(updated,expected_identity)
            # This is exactly the validation used by the explicit Validate button.
            with catalog.connect() as db:
                write_lock(db);checked_context(db,job)
                db.execute('UPDATE listing_generation_jobs SET report_json=?,message=?,updated_at=? WHERE id=?',
                    (encoded(report),'正在保存最终店铺校验结果',int(time.time()),jid))
            validation=validate_document(catalog,job['batch_id'],job['revision'],remote,bound)
            for sku,entry in report['rows'].items():
                if entry['state']=='skipped':
                    continue
                errors=validation['rows'][sku]['errors']
                paths={i['path'] for i in summarize_generation_issues(errors)}
                explanations=[i for i in entry.get('generation_issues',[]) if i['path'] in paths]
                entry['issues']=explanations+errors
                entry['state']='review' if errors else 'complete'
                entry['preparation_state']=('needs_prerequisite' if any(prerequisite_issue(i) for i in errors) else 'needs_review') if errors else 'ready'
            with catalog.connect() as db:
                write_lock(db);stored,_=checked_context(db,job)
                # Persist any evidence conflicts in the same final validation.
                for sku,entry in report['rows'].items():
                    if entry['state']!='skipped' and entry['issues']:
                        validation['rows'][sku]=dict(valid=False,errors=entry['issues'])
                validation['valid_count']=sum(r['valid'] for r in validation['rows'].values())
                db.execute('UPDATE walmart_listing_documents SET validation_json=? WHERE batch_id=?',(encoded(validation),job['batch_id']))
                schemas={r['product_type']:cache_value(db,bound,'spec',stored['version'],r['product_type'])
                    for r in json.loads(stored['rows_json']) if r['product_type']}
                identity=fingerprint(dict(rows=json.loads(stored['rows_json']),basis=json.loads(stored['basis_json']),
                    version=stored['version'],rules=RULES_VERSION,model=vision_identity(ai),feedback=latest_results(db,job['batch_id']),
                    features=[getattr(ai.settings,'vision',False),getattr(ai.settings,'supplement',False)],resources=resource_digest(db,bound),
                    schemas={pt:fingerprint(s) for pt,s in schemas.items() if s},partner=bound['partner_id'],binding_revision=bound['revision']))
                report.pop('previous_fields',None)
                report['usage']=getattr(ai,'usage_history',[])
                report['validation_id']=validation['id'];report['validated_at']=validation['at']
                count=sum(e.get('preparation_state')=='ready' for e in report['rows'].values())
                db.execute("UPDATE listing_generation_jobs SET state='complete',input_hash=?,report_json=?,message=?,updated_at=? WHERE id=?",
                    (identity,encoded(report),f'生成及校验结束：{count} 个 SKU 可提交，其余已进入异常队列',int(time.time()),jid))
                db.execute('INSERT INTO walmart_listing_events VALUES(?,?,?,?,?,?)',
                    (uuid.uuid4().hex,job['batch_id'],job['actor_id'],'generate',now(),encoded({'job_id':jid,'rules':RULES_VERSION})))
    except Exception as exc:
        message=str(exc) if isinstance(exc,ListingAIError) else '生成任务中断：店铺、来源、规格或连接异常；已保存资料保留，可重试未完成项'
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
            return dict(job=public_job(latest(db,bid),db),ai=ListingAISettings().public())

    @router.post('')
    def start(bid: str,request: Request):
        # Re-read app.state so tests and provider adapters can inject their client.
        return start_job(catalog,request.app.state.walmart_publisher,bid,
                         getattr(request.state,'user',{}).get('id'),source_mode='shared')

    from .listing_shared_collection import SourceReceipts, resume_sources

    @router.post('/{jid}/sources')
    def resume(bid: str, jid: str, data: SourceReceipts, request: Request):
        return resume_sources(catalog,request.app.state.walmart_publisher,bid,jid,data.captures,
                              getattr(request.state,'user',{}).get('id'))

    @router.post('/{jid}/cancel-sources')
    def cancel_sources(bid: str, jid: str, request: Request):
        with catalog.connect() as db:
            write_lock(db)
            row=db.execute('SELECT * FROM listing_generation_jobs WHERE id=? AND batch_id=?',(jid,bid)).fetchone()
            if not row or row['actor_id']!=getattr(request.state,'user',{}).get('id'):
                raise HTTPException(409,'该采集任务不属于当前批次或操作者')
            if row['state']!='awaiting_sources':
                raise HTTPException(409,'任务已进入生成阶段，请刷新查看进度')
            db.execute("UPDATE listing_generation_jobs SET state='interrupted',message=?,updated_at=? WHERE id=?",
                       ('本轮采集已停止，可重新采集；商品库已取得的记录保留',int(time.time()),jid))
            return public_job(db.execute('SELECT * FROM listing_generation_jobs WHERE id=?',(jid,)).fetchone())
    return router
