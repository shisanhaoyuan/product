import csv
import io
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlparse
from fastapi import Depends, FastAPI, HTTPException, Query, Request
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
    app=FastAPI(title='商品库 API',version='1.0.0',docs_url=None,redoc_url=None,description='独立商品库；仅本系统数据库读写，不会修改或刊登 ERP 商品。')
    app.state.catalog=catalog
    app.state.auth=auth
    app.include_router(auth_routes(auth,secure_cookie))
    app.include_router(blacklist_routes(catalog))
    app.include_router(prelisting_routes(catalog))
    app.include_router(editing_routes(catalog))
    storage=configured_storage(catalog)
    app.state.image_storage=storage
    app.include_router(image_routes(catalog,storage))
    app.include_router(source_routes(catalog))
    from .giga import giga_routes
    app.include_router(giga_routes(catalog))

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
        if auth_enabled and protected and path not in public_paths:
            user=auth.session(request.cookies.get(COOKIE))
            if not user:
                return Response('请先登录',status_code=401,headers={'Cache-Control':'no-store'})
            request.state.user=user
            if user['must_change_password'] and path not in {'/api/auth/password','/api/auth/logout'}:
                return Response('首次登录请先修改密码',status_code=403)
            if path.startswith('/api/admin/') and user['role'] != 'admin':
                return Response('需要管理员权限',status_code=403)
            if writing and (path == '/api/imports' or path.startswith(('/api/seller-blacklist', '/api/prelisting', '/api/listing-stores', '/api/products/', '/api/integrations/'))) and user['role'] not in {'admin','editor'}:
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
                if length and (int(length)<0 or int(length)>20*1024*1024):
                    return Response('请求不能超过 20 MB',status_code=413)
            except ValueError:
                return Response('无效 Content-Length',status_code=400)
        result=await call_next(request)
        if protected:
            result.headers['Cache-Control']='no-store'
        result.headers['X-Content-Type-Options']='nosniff'
        result.headers['Referrer-Policy']='no-referrer'
        result.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' https: http: data:; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return result

    @app.get('/api/health')
    def health():
        with catalog.connect() as db:
            db.execute('SELECT 1').fetchone()
        return {'status':'ok','schema_version':13,
                'database':'postgresql' if catalog.postgres else 'sqlite',
                'images':storage.backend if storage else 'sqlite'}

    @app.get('/api/stats')
    def stats(source:Literal["", "Amazon", "GigaB2B"]=""):return catalog.stats(source=source)

    def listing_counts(listing_count_min: int | None = Query(None, ge=1, le=2147483647),
                       listing_count_max: int | None = Query(None, ge=1, le=2147483647)):
        if listing_count_min is not None and listing_count_max is not None and listing_count_min > listing_count_max:
            raise HTTPException(422, '上架次数下限不能大于上限')
        return dict(listing_count_min=listing_count_min, listing_count_max=listing_count_max)

    @app.get('/api/products')
    def products(q:str='',store:str='',brand:str='',status:str='',date:str='',page:int=Query(1,ge=1),page_size:int=Query(24,ge=1,le=100),category:str='',seller_status:str=Query('',pattern='^(blocked|unblocked)?$'),deduplicate:bool=False,counts:dict=Depends(listing_counts),source:Literal['', 'Amazon', 'GigaB2B']=''):
        return catalog.find(q,store,brand,status,date,page,page_size,category=category,seller_status=seller_status,deduplicate=deduplicate,source=source,**counts)

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
            bundle=ImportBundle.model_validate_json(raw)
            return catalog.ingest(bundle,actor_id=request.state.user['id'] if auth else None)
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
    def export_csv(q:str='',store:str='',brand:str='',status:str='',date:str='',category:str='',seller_status:str=Query('',pattern='^(blocked|unblocked)?$'),counts:dict=Depends(listing_counts),source:Literal['', 'Amazon', 'GigaB2B']=''):
        buffer=io.StringIO();writer=csv.writer(buffer)
        writer.writerow(['ASIN','商品SKU','平台SKU','店铺','品牌','商品来源','来源链接','商品卖家','标题','子变体数','保存确认','填写完整','证据类型','上传时间','商品类目','卖家黑名单原因'])
        page=1
        def safe(value):
            text='' if value is None else str(value)
            return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
        while True:
            found=catalog.find(q,store,brand,status,date,page,100,category=category,seller_status=seller_status,source=source,**counts)
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
