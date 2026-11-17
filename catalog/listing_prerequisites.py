"""Admin-managed real identifiers and store-scoped, verified prerequisites."""
import json
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .database import write_lock, is_postgres
from .prelisting import encoded, fingerprint
from .credential_vault import decrypt, VaultError
from .walmart_listing_schema import identifier_errors
from .walmart_client import WalmartError


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS listing_store_preparation(
        store_id INTEGER PRIMARY KEY REFERENCES listing_stores(id),partner_id TEXT NOT NULL,
        revision INTEGER NOT NULL,data_json TEXT NOT NULL,updated_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS listing_real_identifiers(
        code_key TEXT PRIMARY KEY,kind TEXT NOT NULL,value TEXT NOT NULL,brand TEXT NOT NULL,
        proof TEXT NOT NULL,source_system TEXT NOT NULL,source_sku TEXT NOT NULL,
        package_key TEXT NOT NULL,actor_id INTEGER,created_at TEXT NOT NULL)''')
    db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS listing_identifier_product
        ON listing_real_identifiers(source_system,source_sku,brand,package_key) WHERE source_sku<>'' ''')
    db.execute('''CREATE TABLE IF NOT EXISTS listing_preparation_rounds(
        job_id TEXT NOT NULL REFERENCES listing_generation_jobs(id),platform_sku TEXT NOT NULL,
        round INTEGER NOT NULL,input_digest TEXT NOT NULL,state TEXT NOT NULL,
        data_json TEXT NOT NULL,updated_at BIGINT NOT NULL,
        PRIMARY KEY(job_id,platform_sku,round))''')
    db.execute('''CREATE TABLE IF NOT EXISTS listing_vision_cache(
        digest TEXT PRIMARY KEY,data_json TEXT NOT NULL,created_at BIGINT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS listing_result_tasks(
        attempt_id TEXT PRIMARY KEY REFERENCES walmart_listing_attempts(id),actor_id INTEGER,
        state TEXT NOT NULL,next_at BIGINT NOT NULL,tries INTEGER NOT NULL,
        token TEXT NOT NULL,lease_until BIGINT NOT NULL,last_error TEXT NOT NULL,
        repair_job_id TEXT NOT NULL DEFAULT '')''')
    columns = ({r[0] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='listing_result_tasks'")}
               if is_postgres(db) else {r[1] for r in db.execute('PRAGMA table_info(listing_result_tasks)')})
    if 'repair_job_id' not in columns:
        db.execute("ALTER TABLE listing_result_tasks ADD COLUMN repair_job_id TEXT NOT NULL DEFAULT ''")
    db.execute('''CREATE TABLE IF NOT EXISTS listing_identifier_events(
        id TEXT PRIMARY KEY,code_key TEXT NOT NULL REFERENCES listing_real_identifiers(code_key),
        actor_id INTEGER,action TEXT NOT NULL,details_json TEXT NOT NULL,at BIGINT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS listing_image_evidence(
        listing_id TEXT NOT NULL,source_sku TEXT NOT NULL,url_hash TEXT NOT NULL,
        digest TEXT NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,created_at BIGINT NOT NULL,
        PRIMARY KEY(listing_id,source_sku,url_hash,digest))''')
    db.execute('''CREATE TABLE IF NOT EXISTS listing_preparation_blockers(
        batch_id TEXT NOT NULL,platform_sku TEXT NOT NULL,data_json TEXT NOT NULL,
        PRIMARY KEY(batch_id,platform_sku))''')
    db.execute('INSERT INTO migrations VALUES(18) ON CONFLICT DO NOTHING')


def preparation_settings(db, bound):
    row=db.execute('SELECT * FROM listing_store_preparation WHERE store_id=?',(bound['store_id'],)).fetchone()
    if not row or row['partner_id']!=bound['partner_id']:
        return dict(revision=0,fulfillment_center_id='',exemptions=[])
    return json.loads(row['data_json']) | {'revision':row['revision']}


def resource_digest(db, bound):
    # Assignment is transactional but does not change available business evidence.
    codes=[dict(r) for r in db.execute('SELECT code_key,brand,proof FROM listing_real_identifiers ORDER BY code_key')]
    return fingerprint(dict(settings=preparation_settings(db,bound),codes=codes))


def evidence_errors(db, doc, row, item):
    record=db.execute('SELECT data_json FROM listing_preparation_blockers WHERE batch_id=? AND platform_sku=?',
                      (doc['batch_id'],row['platform_sku'])).fetchone()
    if not record:
        return []
    blockers=json.loads(record[0]);issues=[]
    for blocker in blockers:
        if blocker.get('source_digest')!=item['source_digest']:
            continue
        if blocker.get('path')=='product_type' and blocker.get('value')!=row['product_type']:
            continue
        issues.append({k:blocker[k] for k in ('path','message')})
    return issues


def identifier_ownership_errors(db,doc,row,item):
    identity=row['identification']
    if identity['mode']!='identifier' or not identity.get('value'):
        return []
    key=identity['value'].zfill(14);brand=row['visible'].get('brand','')
    assigned=db.execute('SELECT * FROM listing_real_identifiers WHERE code_key=?',(key,)).fetchone()
    errors=[]
    if assigned and (assigned['brand']!=brand or assigned['source_system']!=item['source'] or assigned['source_sku']!=row['source_sku']):
        errors.append(dict(path='identification',message='真实编码池归属与当前来源 SKU 或品牌不符，不能借用其他商品编码'))
    for other in json.loads(doc['rows_json']):
        code=other['identification']
        if (other['platform_sku']!=row['platform_sku'] and code['mode']=='identifier' and code.get('value')
                and code['value'].zfill(14)==key and (other['listing_id'],other['source_sku'])!=(row['listing_id'],row['source_sku'])):
            errors.append(dict(path='identification',message='不同子 SKU 使用了同一真实编码，请按商品归属更正'))
            break
    source=db.execute('SELECT data_json FROM current_listings WHERE id=?',(row['listing_id'],)).fetchone()
    source_brand=json.loads(source[0]).get('brand','') if source else ''
    if source_brand and source_brand.casefold()!=str(brand).casefold() and not assigned:
        errors.append(dict(path='identification',message='来源品牌与目标品牌不同，请使用目标品牌真实编码池或已批准豁免'))
    return errors


def validation_current(db, doc, bound, report):
    if (not report or report.get('revision')!=doc['revision'] or report.get('at',0)<time.time()-600
            or not bound or bound['status']!='verified' or not bound['encrypted_credentials']
            or report.get('binding_revision')!=bound['revision'] or report.get('partner_id')!=bound['partner_id']
            or report.get('resources_digest')!=resource_digest(db,bound)):
        return False
    from .walmart_publishing import unchanged_sources, cache_value, latest_results
    if report.get('feedback_digest')!=fingerprint(latest_results(db,doc['batch_id'])):
        return False
    try:
        unchanged_sources(db,doc)
    except HTTPException:
        return False
    for pt,digest in report.get('schema_digests',{}).items():
        schema=cache_value(db,bound,'spec',doc['version'],pt)
        if not schema or fingerprint(schema)!=digest:
            return False
    return True


def pool_assignment(db, source_system, source_sku, brand):
    identity=(source_system,source_sku,brand,'each')
    row=db.execute('''SELECT * FROM listing_real_identifiers WHERE
        source_system=? AND source_sku=? AND brand=? AND package_key=?''',identity).fetchone()
    if not row:
        row=db.execute("SELECT * FROM listing_real_identifiers WHERE source_sku='' AND brand=? ORDER BY created_at,code_key LIMIT 1",(brand,)).fetchone()
        if row:
            db.execute('UPDATE listing_real_identifiers SET source_system=?,source_sku=?,package_key=? WHERE code_key=? AND source_sku=?',
                       (source_system,source_sku,'each',row['code_key'],''))
            db.execute('INSERT INTO listing_identifier_events VALUES(?,?,?,?,?,?)',
                (uuid.uuid4().hex,row['code_key'],None,'assign',encoded(dict(source_system=source_system,source_sku=source_sku,brand=brand,package_key='each')),int(time.time())))
    return dict(row) if row else None


def apply_prerequisites(catalog, bound, row, item, nodes, remote, audit, guard):
    """Only this deterministic layer may allocate real codes; never the model."""
    with catalog.connect() as db:
        settings=preparation_settings(db,bound)
    node=settings.get('fulfillment_center_id')
    if not row['fulfillment_center_id'] and node in {n['id'] for n in nodes}:
        row['fulfillment_center_id']=node
        audit['fulfillment_center_id']=dict(kind='user_rule',value=node,refs=[],reason='目标店铺已配置并核验的默认发货仓')
    identity=row['identification']
    if identity.get('value') or identity.get('submission_id'):
        return
    brand=row['visible'].get('brand','')
    candidates=[r for r in settings.get('exemptions',[]) if r['brand']==brand and r['product_type']==row['product_type']]
    if len(candidates)==1:
        configured=candidates[0]
        from .walmart_publishing import approved_exemption
        if approved_exemption(remote,row,configured['category'],configured['submission_id']):
            row['identification']=dict(mode='exemption',type=identity['type'],value='',submission_id=configured['submission_id'])
            audit['identification']=dict(kind='verified_prerequisite',value=row['identification'],refs=[],
                reason='当前店铺、品牌、类目及商品类型的已批准豁免已在线核验')
            return
    with catalog.connect() as db:
        write_lock(db);guard(db)
        assigned=pool_assignment(db,item['source'],row['source_sku'],brand)
    if assigned:
        row['identification']=dict(mode='identifier',type=assigned['kind'],value=assigned['value'],submission_id='')
        audit['identification']=dict(kind='verified_prerequisite',value=row['identification'],refs=[],
            reason='管理员导入的真实编码已按来源、子 SKU、品牌和包装层级事务绑定')


class Exemption(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    brand:str=Field(min_length=1,max_length=200)
    product_type:str=Field(min_length=1,max_length=200)
    category:str=Field(min_length=1,max_length=200)
    submission_id:str=Field(min_length=1,max_length=200)


class SettingsInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    revision:int=Field(ge=0)
    fulfillment_center_id:str=Field(default='',max_length=200)
    exemptions:list[Exemption]=Field(default_factory=list,max_length=100)


class RealCode(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    type:str=Field(pattern='^(EAN|UPC|GTIN)$')
    value:str=Field(min_length=1,max_length=14)
    brand:str=Field(min_length=1,max_length=200)
    proof:str=Field(min_length=3,max_length=1000)
    source_system:str=Field(default='',max_length=100)
    source_sku:str=Field(default='',max_length=200)


class CodeImport(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    items:list[RealCode]=Field(min_length=1,max_length=1000)


def prerequisite_routes(catalog):
    router=APIRouter(prefix='/api/walmart')

    @router.get('/accounts/{sid}/preparation')
    def read(sid:int):
        from .walmart_publishing import binding
        with catalog.connect() as db:
            _,bound=binding(db,sid)
            return preparation_settings(db,bound)

    @router.post('/accounts/{sid}/preparation/options')
    def options(sid:int,request:Request):
        from .walmart_publishing import binding, normalize_nodes, cache_put
        from .walmart_listing_schema import taxonomy_options
        with catalog.connect() as db:
            _,bound=binding(db,sid)
        try:
            with request.app.state.walmart_publisher.session(decrypt(sid,bound['encrypted_credentials']),bound['partner_id']) as remote:
                nodes=normalize_nodes(remote.nodes());types=taxonomy_options(remote.taxonomy())
            with catalog.connect() as db:
                write_lock(db);_,live=binding(db,sid,bound['partner_id'])
                if live['revision']!=bound['revision']:
                    raise HTTPException(409,'店铺绑定已改变，请刷新')
                cache_put(db,bound,'nodes',nodes);cache_put(db,bound,'taxonomy',types)
            return dict(nodes=nodes,types=types)
        except (WalmartError,VaultError) as exc:
            raise HTTPException(400,str(exc)) from None

    @router.put('/accounts/{sid}/preparation')
    def save(sid:int,data:SettingsInput,request:Request):
        from .walmart_publishing import binding, normalize_nodes, approved_exemption
        from .walmart_listing_schema import taxonomy_options
        with catalog.connect() as db:
            _,bound=binding(db,sid)
        try:
            with request.app.state.walmart_publisher.session(decrypt(sid,bound['encrypted_credentials']),bound['partner_id']) as remote:
                if data.fulfillment_center_id and data.fulfillment_center_id not in {n['id'] for n in normalize_nodes(remote.nodes())}:
                    raise HTTPException(422,'默认仓库必须是该店铺实际启用的仓库')
                if data.exemptions:
                    taxonomy={t['name']:t['category'] for t in taxonomy_options(remote.taxonomy())}
                    seen=set()
                    for ex in data.exemptions:
                        key=(ex.brand,ex.product_type)
                        if key in seen or taxonomy.get(ex.product_type)!=ex.category:
                            raise HTTPException(422,'豁免商品类型/类目不匹配，或同品牌商品类型重复配置')
                        seen.add(key)
                        row=dict(product_type=ex.product_type,visible={'brand':ex.brand})
                        if not approved_exemption(remote,row,ex.category,ex.submission_id):
                            raise HTTPException(422,'未查到该店铺、品牌及类目准确的 APPROVED 豁免')
            with catalog.connect() as db:
                write_lock(db);_,live=binding(db,sid,bound['partner_id'])
                current=preparation_settings(db,live)
                if current['revision']!=data.revision or live['revision']!=bound['revision']:
                    raise HTTPException(409,'店铺或生成配置已变化，请刷新')
                record=data.model_dump(exclude={'revision'})
                db.execute('''INSERT INTO listing_store_preparation VALUES(?,?,?,?,?) ON CONFLICT(store_id)
                    DO UPDATE SET partner_id=excluded.partner_id,revision=excluded.revision,data_json=excluded.data_json,updated_at=excluded.updated_at''',
                    (sid,bound['partner_id'],data.revision+1,encoded(record),datetime.now(timezone.utc).isoformat()))
                # Configuration affects readiness, including drafts in other tabs.
                for b in db.execute('SELECT id,preview_json FROM prelisting_batches'):
                    if json.loads(b['preview_json'])['store']['id']==sid:
                        db.execute("UPDATE walmart_listing_documents SET validation_json='{}' WHERE batch_id=?",(b['id'],))
                from .walmart_accounts import event
                event(db,sid,getattr(request.state,'user',{}).get('id',0),'preparation_settings','已更新并核验生成前提配置')
                return record | {'revision':data.revision+1}
        except (WalmartError,VaultError) as exc:
            raise HTTPException(400,str(exc)) from None

    @router.get('/identifiers')
    def codes():
        with catalog.connect() as db:
            return {'items':[dict(r) for r in db.execute('SELECT * FROM listing_real_identifiers ORDER BY created_at DESC,code_key LIMIT 1000')]}

    @router.post('/identifiers')
    def import_codes(data:CodeImport,request:Request):
        prepared=[]
        for item in data.items:
            if identifier_errors(dict(mode='identifier',type=item.type,value=item.value)):
                raise HTTPException(422,'编码格式或校验位不正确，保留前导零并使用真实编码')
            if bool(item.source_system)!=bool(item.source_sku):
                raise HTTPException(422,'来源系统和来源 SKU 必须同时指定或同时留空')
            prepared.append((item.value.zfill(14),item))
        if len({k for k,_ in prepared})!=len(prepared):
            raise HTTPException(422,'导入中包含重复或等价编码')
        with catalog.connect() as db:
            write_lock(db)
            for key,item in prepared:
                existing=db.execute('SELECT * FROM listing_real_identifiers WHERE code_key=?',(key,)).fetchone()
                if existing:
                    if any(existing[k]!=v for k,v in [('brand',item.brand),('proof',item.proof)]) or (item.source_sku and
                            (existing['source_system'],existing['source_sku'])!=(item.source_system,item.source_sku)):
                        raise HTTPException(409,'真实编码已有其他归属，不能覆盖或重新分配')
                    continue
                if item.source_sku and db.execute('SELECT 1 FROM listing_real_identifiers WHERE source_system=? AND source_sku=? AND brand=? AND package_key=?',
                        (item.source_system,item.source_sku,item.brand,'each')).fetchone():
                    raise HTTPException(409,'该来源 SKU 和品牌已绑定真实编码')
                db.execute('INSERT INTO listing_real_identifiers VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (key,item.type,item.value,item.brand,item.proof,item.source_system,item.source_sku,'each',
                     getattr(request.state,'user',{}).get('id'),datetime.now(timezone.utc).isoformat()))
                db.execute('INSERT INTO listing_identifier_events VALUES(?,?,?,?,?,?)',
                    (uuid.uuid4().hex,key,getattr(request.state,'user',{}).get('id'),'import',encoded(item.model_dump()),int(time.time())))
        return dict(imported=len(prepared))

    return router
