import asyncio
from contextlib import asynccontextmanager, suppress
import csv
import io
import json
import os
import secrets
import anyio
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from typing import Literal
from .db import Catalog
from .models import ImportBundle
from .auth import AuthStore, COOKIE, auth_routes
from .blacklist import blacklist_routes
from .prelisting import prelisting_routes
from .editing import editing_routes
from .images import image_routes
from .source_capture import source_routes
from .object_storage import configured_storage
from .product_search import normalize_identifiers
from .product_ranges import ProductRanges
from .database import DatabaseBusy
from .concurrency import AdmissionMiddleware, RequestGate, busy_response, setting
from starlette.concurrency import run_in_threadpool

ROOT=Path(__file__).resolve().parent.parent


def create_app(database=None):
    catalog=Catalog(database or os.environ.get('CATALOG_DATABASE_URL') or os.environ.get('CATALOG_DB',ROOT/'data/catalog.db'))
    auth_enabled=os.environ.get('CATALOG_AUTH_ENABLED','0') == '1'
    public_origin=os.environ.get('CATALOG_PUBLIC_ORIGIN','').rstrip('/')
    secure_cookie=os.environ.get('CATALOG_COOKIE_SECURE','1') != '0'
    if auth_enabled:
        origin_url=urlparse(public_origin)
        if origin_url.scheme not in {'https','http'} or not origin_url.netloc or origin_url.path or origin_url.query or origin_url.fragment or origin_url.username or origin_url.password:
            raise RuntimeError('团队模式需要有效的 CATALOG_PUBLIC_ORIGIN，例如 https://catalog.example.com')
        if (origin_url.scheme != 'https' or not secure_cookie) and origin_url.hostname not in {'127.0.0.1','localhost'}:
            raise RuntimeError('服务器团队模式必须使用 HTTPS 和 Secure Cookie')
    auth=AuthStore(catalog) if auth_enabled else None
    auth_limiter=anyio.CapacityLimiter(4)
    gate=RequestGate(active=setting('CATALOG_HTTP_ACTIVE',48,maximum=256),
                     waiting=setting('CATALOG_HTTP_WAITING',64,minimum=0,maximum=512),
                     work=setting('CATALOG_HTTP_WORK',8,maximum=32),
                     images=setting('CATALOG_HTTP_IMAGES',8,maximum=32),
                     timeout=setting('CATALOG_HTTP_WAIT_TIMEOUT',2,maximum=10))
    @asynccontextmanager
    async def lifespan(app):
        from .listing_result_worker import serve
        from .listing_generation_jobs import serve_generation, stop_generation
        limiter=anyio.to_thread.current_default_thread_limiter()
        previous_tokens=limiter.total_tokens
        limiter.total_tokens=setting('CATALOG_HTTP_THREADS',32,minimum=16,maximum=128)
        catalog._closing=False
        tasks=[]
        if os.environ.get('CATALOG_LISTING_RESULT_WORKER','1')=='1':
            tasks.append(asyncio.create_task(serve(app)))
        tasks.append(asyncio.create_task(serve_generation(app)))
        from .amazon_import import fallback_worker
        tasks.append(asyncio.create_task(fallback_worker(catalog)))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            stop_generation(catalog)
            limiter.total_tokens=previous_tokens

    app=FastAPI(lifespan=lifespan,title='商品库 API',version='1.0.0',docs_url=None,redoc_url=None,description='独立商品库；可通过绑定店铺明确提交沃尔玛商品，不操作 ERP。')
    app.state.catalog=catalog
    app.state.auth=auth
    app.state.request_gate=gate
    app.include_router(auth_routes(auth,secure_cookie))
    app.include_router(blacklist_routes(catalog))
    app.include_router(prelisting_routes(catalog))
    app.include_router(editing_routes(catalog))
    storage=configured_storage(catalog)
    app.state.image_storage=storage
    app.include_router(image_routes(catalog,storage))
    app.include_router(source_routes(catalog))
    from .amazon_import import routes as amazon_import_routes
    app.include_router(amazon_import_routes(catalog))
    from .giga import giga_routes
    app.include_router(giga_routes(catalog))
    app.include_router(giga_routes(catalog, 'CA'))
    from .saleyee import saleyee_routes
    app.include_router(saleyee_routes(catalog))
    from .giga_patrol import patrol_routes
    app.include_router(patrol_routes(catalog))
    app.include_router(patrol_routes(catalog, 'saleyee'))
    app.include_router(patrol_routes(catalog, 'giga-canada'))
    from .walmart_client import WalmartVerifier
    from .walmart_accounts import walmart_routes
    app.state.walmart_verifier=WalmartVerifier()
    app.include_router(walmart_routes(catalog))
    from .walmart_publishing_client import WalmartPublishingClient
    from .walmart_publishing import publishing_routes, public_media_routes
    app.state.walmart_publisher=WalmartPublishingClient()
    app.include_router(publishing_routes(catalog))
    from .store_items import store_item_routes
    app.include_router(store_item_routes(catalog))
    from .walmart_review import review_routes
    app.include_router(review_routes(catalog))
    app.include_router(public_media_routes(catalog,storage))
    from .listing_generation_jobs import generation_routes
    app.include_router(generation_routes(catalog,app.state.walmart_publisher))
    from .listing_prerequisites import prerequisite_routes
    app.include_router(prerequisite_routes(catalog))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if request.url.path.startswith('/api/prelisting/') and '/walmart' in request.url.path:
            # Do not echo arbitrary payloads (including non-finite numbers) in errors.
            return JSONResponse({'detail':'刊登资料格式不正确，请检查商品编码、SKU、数值及资料版本'},status_code=422)
        return await request_validation_exception_handler(request,exc)

    @app.exception_handler(DatabaseBusy)
    async def database_busy(request,exc):
        return busy_response()

    @app.middleware('http')
    async def guard(request:Request, call_next):
        path=request.url.path
        writing=request.method not in ('GET','HEAD','OPTIONS')
        if auth_enabled and request.headers.get('host') != urlparse(public_origin).netloc:
            return Response('访问域名不匹配',status_code=400)
        if not auth_enabled and (path.startswith('/api/admin/') or path.startswith('/api/auth/') and path != '/api/auth/me'):
            return Response('未启用团队模式',status_code=404)
        public_paths={'/api/health','/api/auth/me','/api/auth/login'}
        protected=path.startswith('/api/') or path in {'/docs','/openapi.json'}
        public_listing_image=request.method in ('GET','HEAD') and path.startswith('/api/listing-media/')
        if auth_enabled and protected and path not in public_paths and not public_listing_image:
            try:
                user=await anyio.to_thread.run_sync(auth.session,request.cookies.get(COOKIE),limiter=auth_limiter)
            except DatabaseBusy:
                return busy_response()
            if not user:
                return Response('请先登录',status_code=401,headers={'Cache-Control':'no-store'})
            request.state.user=user
            if user['must_change_password'] and path not in {'/api/auth/password','/api/auth/logout'}:
                return Response('首次登录请先修改密码',status_code=403)
            if path.startswith(('/api/admin/', '/api/walmart/')) and user['role'] != 'admin':
                return Response('需要管理员权限',status_code=403)
            if writing and (path == '/api/imports' or path.startswith(('/api/seller-blacklist', '/api/prelisting', '/api/listing-stores', '/api/products/', '/api/integrations/', '/api/store-items'))) and user['role'] not in {'admin','editor'}:
                return Response('只读成员不能修改商品库数据',status_code=403)
            if writing and not secrets.compare_digest(request.headers.get('x-csrf-token',''),user['csrf_token']):
                return Response('登录校验已过期，请刷新后重试',status_code=403)
        if writing:
            token=os.environ.get('CATALOG_IMPORT_TOKEN','')
            if path == '/api/imports' and token and not secrets.compare_digest(request.headers.get('x-import-token',''),token):
                return Response('需要正确的 X-Import-Token',status_code=401)
            origin=request.headers.get('origin')
            if auth_enabled and origin != public_origin:
                return Response('不允许跨站写入',status_code=403)
            if not auth_enabled and origin and urlparse(origin).netloc!=request.headers.get('host'):
                return Response('不允许跨站写入',status_code=403)
            length=request.headers.get('content-length')
            try:
                limit_mb=150 if path.rstrip('/')=='/api/integrations/amazon-collector/spreadsheet' else 20
                if length and (int(length)<0 or int(length)>limit_mb*1024*1024):
                    return Response(f'请求不能超过 {limit_mb} MB',status_code=413)
            except ValueError:
                return Response('无效 Content-Length',status_code=400)
        result=await call_next(request)
        cacheable_image = (request.method == 'GET' and result.status_code in (200, 304)
                           and getattr(request.state, 'cacheable_product_image', False))
        if protected and not cacheable_image:
            result.headers['Cache-Control']='no-store'
        result.headers['X-Content-Type-Options']='nosniff'
        result.headers['Referrer-Policy']='no-referrer'
        result.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' https: http: data:; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return result

    # Added last so admission happens before session lookup, request body reads
    # and synchronous route dispatch. Long requests cannot fill every lane.
    app.add_middleware(AdmissionMiddleware,gate=gate)

    @app.get('/api/admin/runtime')
    def runtime():
        from .listing_generation_jobs import POOL
        from .source_images import IMAGE_POOL
        from .listing_ai_client import ListingAISettings
        from .ai_dispatch import key_pool
        ai=ListingAISettings()
        channels=key_pool(ai.base_url,ai.keys,ai.key_parallel).stats() if ai.keys else dict(keys=0,active=0,cooling=0)
        with catalog.connect() as db:
            jobs={row['state']:row['count'] for row in db.execute('''SELECT state,COUNT(*) AS count FROM listing_generation_jobs
                WHERE state IN ('queued','running','awaiting_sources') GROUP BY state''').fetchall()}
        generation={**POOL.stats(),'per_user_limit':setting('CATALOG_GENERATION_PER_USER',2,maximum=5),'jobs':jobs}
        return dict(requests=gate.stats(),generation=generation,images=IMAGE_POOL.stats(),ai=channels,
                    database=({'backend':'postgresql',**catalog.pool().get_stats()} if catalog.postgres
                              else {'backend':'sqlite'}))

    @app.get('/api/health')
    def health():
        with catalog.connect() as db:
            db.execute('SELECT 1').fetchone()
        return {'status':'ok','schema_version':21,
                'database':'postgresql' if catalog.postgres else 'sqlite',
                'images':storage.backend if storage else 'sqlite'}

    @app.get('/api/stats')
    def stats(source:Literal["", "Amazon", "Amazon_CA", "GigaB2B", "GigaB2B_CA", "Saleyee"]=""):return catalog.stats(source=source)

    def listing_counts(listing_count_min: int | None = Query(None, ge=1, le=2147483647),
                       listing_count_max: int | None = Query(None, ge=1, le=2147483647)):
        if listing_count_min is not None and listing_count_max is not None and listing_count_min > listing_count_max:
            raise HTTPException(422, '上架次数下限不能大于上限')
        return dict(listing_count_min=listing_count_min, listing_count_max=listing_count_max)

    def product_ranges(stock_min: int | None = Query(None, ge=0, le=2147483647),
                       stock_max: int | None = Query(None, ge=0, le=2147483647),
                       price_min: Decimal | None = Query(None, ge=0, le=Decimal('9999999999.99'), max_digits=12, decimal_places=2),
                       price_max: Decimal | None = Query(None, ge=0, le=Decimal('9999999999.99'), max_digits=12, decimal_places=2)):
        try:
            return ProductRanges(stock_min=stock_min, stock_max=stock_max,
                                 price_min=price_min, price_max=price_max).model_dump()
        except ValidationError as exc:
            raise HTTPException(422, exc.errors()[0]['msg'].removeprefix('Value error, ')) from None

    def batch_identifiers(identifiers: str = Query('', max_length=6000)):
        try:
            return normalize_identifiers(identifiers)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    def multi_filters(multi: str = Query('', max_length=30000)):
        from .product_filters import normalize_multi
        try:
            return normalize_multi(multi)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get('/api/products')
    def products(q:str='',store:str='',brand:str='',status:str='',date:str='',page:int=Query(1,ge=1),page_size:int=Query(50,ge=1,le=500),category:str='',seller_status:str=Query('',pattern='^(blocked|unblocked)?$'),deduplicate:bool=False,counts:dict=Depends(listing_counts),ranges:dict=Depends(product_ranges),source:Literal['', 'Amazon', 'Amazon_CA', 'GigaB2B', 'GigaB2B_CA', 'Saleyee']='',inventory_status:Literal['', 'out_of_stock', 'in_stock']='',identifiers:str=Depends(batch_identifiers),listed_store:str=Query('',pattern='^([0-9]+)?$'),price_change:Literal['', 'up', 'down', 'unchanged', 'unknown']='',multi:str=Depends(multi_filters),shipping_address:str=Query('',pattern='^([a-f0-9]{64})?$'),return_address:str=Query('',pattern='^([a-f0-9]{64})?$')):
        return catalog.find(q,store,brand,status,date,page,page_size,category=category,seller_status=seller_status,deduplicate=deduplicate,source=source,inventory_status=inventory_status,identifiers=identifiers,listed_store=listed_store,price_change=price_change,multi=multi,shipping_address=shipping_address,return_address=return_address,**counts,**ranges)

    @app.get('/api/products/{listing_id}')
    def detail(listing_id:str):
        data=catalog.get(listing_id)
        if not data:raise HTTPException(404,'商品不存在')
        return data

    @app.get('/api/products/{listing_id}/snapshots/{snapshot_id}')
    def snapshot(listing_id:str,snapshot_id:int):
        with catalog.connect() as db:
            row=db.execute('SELECT data_json FROM snapshots WHERE id=? AND listing_id=?',(snapshot_id,listing_id)).fetchone()
            if not row:raise HTTPException(404,'快照不存在')
            return json.loads(row[0])

    @app.get('/api/imports')
    def imports():return catalog.imports()

    @app.get('/api/import-schema')
    def import_schema():return ImportBundle.model_json_schema()

    @app.post('/api/imports')
    async def ingest(request:Request):
        if 'application/json' not in request.headers.get('content-type',''):
            raise HTTPException(415,'需要 application/json')
        raw=bytearray()
        async for chunk in request.stream():
            if len(raw)+len(chunk)>20*1024*1024:raise HTTPException(413,'导入文件不能超过 20 MB')
            raw.extend(chunk)
        try:
            def process():
                bundle=ImportBundle.model_validate_json(raw)
                return catalog.ingest(bundle,actor_id=request.state.user['id'] if auth else None)
            return await run_in_threadpool(process)
        except ValidationError as exc:
            raise HTTPException(422,[{'loc':e['loc'],'msg':e['msg']} for e in exc.errors()])
        except ValueError as exc:raise HTTPException(409,str(exc))

    @app.get('/api/export.json')
    def export_json(namespace:str='digitemperor'):
        rows=catalog.export()
        data={'schema_version':1,'namespace':namespace,'label':'商品库数据备份',
              'listings':[{k:v for k,v in r.items() if k not in {'id','namespace'}} for r in rows if r['namespace']==namespace]}
        return Response(json.dumps(data,ensure_ascii=False,indent=2),media_type='application/json',
                        headers={'Content-Disposition':'attachment; filename="catalog-backup.json"'})

    @app.get('/api/export.csv')
    def export_csv(q:str='',store:str='',brand:str='',status:str='',date:str='',category:str='',seller_status:str=Query('',pattern='^(blocked|unblocked)?$'),counts:dict=Depends(listing_counts),ranges:dict=Depends(product_ranges),source:Literal['', 'Amazon', 'Amazon_CA', 'GigaB2B', 'GigaB2B_CA', 'Saleyee']='',inventory_status:Literal['', 'out_of_stock', 'in_stock']='',identifiers:str=Depends(batch_identifiers),listed_store:str=Query('',pattern='^([0-9]+)?$'),price_change:Literal['', 'up', 'down', 'unchanged', 'unknown']='',multi:str=Depends(multi_filters),shipping_address:str=Query('',pattern='^([a-f0-9]{64})?$'),return_address:str=Query('',pattern='^([a-f0-9]{64})?$')):
        buffer=io.StringIO();writer=csv.writer(buffer)
        writer.writerow(['ASIN','商品SKU','平台SKU','店铺','品牌','商品来源','来源链接','商品卖家','标题','子变体数','保存确认','填写完整','证据类型','上传时间','商品类目','卖家黑名单原因'])
        page=1
        def safe(value):
            text='' if value is None else str(value)
            return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
        while True:
            found=catalog.find(q,store,brand,status,date,page,100,category=category,seller_status=seller_status,source=source,inventory_status=inventory_status,identifiers=identifiers,listed_store=listed_store,price_change=price_change,multi=multi,shipping_address=shipping_address,return_address=return_address,**counts,**ranges)
            for row in found['items']:
                detail=catalog.get(row['id'])
                writer.writerow([safe(v) for v in [row['asin'],row['sku'],detail['platform_sku'],row['store'],row['brand'],row['source'],
                    row['source_url'],row['seller'],row['title'],row['variant_count'],row['save_succeeded'],row['fill_complete'],row['evidence_kind'],row['uploaded_at'],row['category'], '\n'.join(f"{b['seller']}：{b['reason']}" for b in row['seller_blacklist'])]])
            if page*100>=found['total']:break
            page+=1
        return Response('\ufeff'+buffer.getvalue(),media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition':'attachment; filename="product-library.csv"'})

    app.mount('/assets',StaticFiles(directory=ROOT/'web'),name='assets')
    @app.get('/docs',include_in_schema=False)
    def docs():return FileResponse(ROOT/'web/api.html')
    @app.get('/',include_in_schema=False)
    def index():
        frontend=ROOT/'web/dist/index.html'
        if not frontend.is_file():
            raise HTTPException(503,'前端尚未构建，请在 frontend 目录执行 npm ci 和 npm run build')
        return FileResponse(frontend,headers={'Cache-Control':'no-cache'})
    return app


app=create_app()
