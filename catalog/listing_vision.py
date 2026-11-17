"""SKU-scoped image evidence. Never forward cookies, private URLs or credentials."""
import base64
import hashlib
import io
import ipaddress
import json
import os
import secrets
import socket
import time
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image, ImageDraw

from .database import write_lock
from .images import normalize_image, MAX_UPLOAD
from .listing_ai_client import ListingAIError
from .object_storage import configured_storage, save_metadata
from .prelisting import encoded, fingerprint


def allowed_image_url(url):
    parsed=urlsplit(url)
    hosts=['media-amazon.com','ssl-images-amazon.com','gigab2b.cn','gigab2b.com']
    hosts += [h.strip().lower() for h in os.environ.get('CATALOG_LISTING_IMAGE_HOSTS','').split(',') if h.strip()]
    host=parsed.hostname or ''
    if (parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443)
            or not any(host==h or host.endswith('.'+h) for h in hosts)):
        raise ListingAIError('图片域名未获准用于服务器识图，请上传本地图片或配置可信图片域名')
    try:
        addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError()
    except (OSError,ValueError):
        raise ListingAIError('图片地址无法安全解析，未读取本机或内网图片') from None


def fetch_image(url, transport=None):
    with httpx.Client(timeout=12,follow_redirects=False,trust_env=False,transport=transport) as client:
        for _ in range(3):
            allowed_image_url(url)
            with client.stream('GET',url) as response:
                if response.status_code in (301,302,303,307,308):
                    url=urljoin(url,response.headers.get('location',''))
                    continue
                if response.status_code!=200:
                    raise ListingAIError('商品图片暂时无法下载，请补采或上传该 SKU 图片')
                data=bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data)>MAX_UPLOAD:
                        raise ListingAIError('识图图片超过8MB，未采用')
                return bytes(data)
    raise ListingAIError('图片重定向过多，未采用')


def image_data(catalog,url,details=None):
    storage=configured_storage(catalog)
    prefix='/api/products/images/assets/'
    if url.startswith(prefix):
        digest=url.removeprefix(prefix)
        if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
            raise ListingAIError('本地图片标识无效')
        with catalog.connect() as db:
            obj=db.execute('SELECT * FROM image_objects WHERE digest=?',(digest,)).fetchone()
            local=None if catalog.postgres else db.execute('SELECT content FROM image_assets WHERE digest=?',(digest,)).fetchone()
        raw=storage.get(obj) if obj and storage else bytes(local[0]) if local else None
        if raw is None:
            raise ListingAIError('该 SKU 引用的本地图片不存在')
    else:
        raw=fetch_image(url)
    data,mime=normalize_image(raw)
    if url.startswith(prefix):
        if hashlib.sha256(raw).hexdigest()!=digest:
            raise ListingAIError('本地图片内容校验失败')
        data=raw  # Keep an existing validated asset stable; do not recompress it each run.
    digest=hashlib.sha256(data).hexdigest()
    metadata=storage.put(digest,data,mime) if storage else None
    with catalog.connect() as db:
        if metadata:
            save_metadata(db,metadata)
        else:
            db.execute('INSERT INTO image_assets VALUES(?,?,?,?,?) ON CONFLICT(digest) DO NOTHING',
                       (digest,data,mime,str(int(time.time())),None))
    with Image.open(io.BytesIO(data)) as original:
        if details is not None:
            details.update(width=original.width,height=original.height)
        image=original.convert('RGB');image.thumbnail((1280,1280))
        out=io.BytesIO();image.save(out,format='JPEG',quality=85)
    return digest,'data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode()


def vision_identity(ai):
    return fingerprint(dict(model=ai.settings.model,base_url=getattr(ai.settings,'base_url',''),
        key_digest=hashlib.sha256(getattr(ai.settings,'key','').encode()).hexdigest(),protocol='vision-v1'))


def capability(catalog,ai):
    key='capability:'+vision_identity(ai)
    with catalog.connect() as db:
        cached=db.execute('SELECT * FROM listing_vision_cache WHERE digest=?',(key,)).fetchone()
    if cached and cached['created_at']>time.time()-86400:
        result=json.loads(cached['data_json'])
        if not result['supported']:
            raise ListingAIError(result['message'])
        return
    colors={'red':'#ff0000','green':'#00ff00','blue':'#0000ff','yellow':'#ffff00','black':'#000000','white':'#ffffff'}
    chosen=[secrets.choice(list(colors)) for _ in range(4)]
    image=Image.new('RGB',(160,160));draw=ImageDraw.Draw(image)
    for i,color in enumerate(chosen):
        x,y=(i%2)*80,(i//2)*80;draw.rectangle((x,y,x+79,y+79),fill=colors[color])
    out=io.BytesIO();image.save(out,format='PNG')
    result=ai.query('vision_capability',dict(_images=['data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()],
        task='Read the four solid quadrant colors, top-left, top-right, bottom-left, bottom-right.',
        output_contract={'colors':['red/green/blue/yellow/black/white, exactly four actual colors']}))
    supported=result.get('colors')==chosen
    record=dict(supported=supported,message='' if supported else '当前模型图片理解能力探测未通过，请检查接口的图片输入支持')
    with catalog.connect() as db:
        db.execute('INSERT INTO listing_vision_cache VALUES(?,?,?) ON CONFLICT(digest) DO UPDATE SET data_json=excluded.data_json,created_at=excluded.created_at',
                   (key,encoded(record),int(time.time())))
    if not supported:
        raise ListingAIError(record['message'])


def image_evidence(catalog,row,variant,ai,schema=None):
    if not getattr(ai.settings,'vision',False):
        return [],dict(state='disabled',images=[])
    urls=list(dict.fromkeys([row['visible'].get('mainImageUrl',''),
        *row['visible'].get('productSecondaryImageURL',[]),*variant.get('content',{}).get('images',[])]))
    urls=[u for u in urls if isinstance(u,str) and u][:6]
    if not urls:
        return [],dict(state='missing',images=[],message='同 SKU 没有可供判断的图片')
    images=[];failures=[]
    for url in urls:
        try:
            dimensions={}
            digest,data=image_data(catalog,url,dimensions)
            width,height=dimensions['width'],dimensions['height']
            with catalog.connect() as db:
                db.execute('INSERT INTO listing_image_evidence VALUES(?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                    (row['listing_id'],row['source_sku'],hashlib.sha256(url.encode()).hexdigest(),digest,width,height,int(time.time())))
            if digest not in [d for d,_ in images]:
                images.append((digest,data))
        except Exception:
            failures.append('一张同 SKU 图片无法安全读取或校验')
    if not images:
        return [],dict(state='failed',images=[],message='图片读取失败，请补采或上传该 SKU 图片')
    capability(catalog,ai)
    from .walmart_listing_schema import sections
    fields={}
    if schema:
        # Include conditional fields too: Sleeper may activate bedSize later.
        fields={k:v for k,v in sections(schema,row['product_type'])['visible'].get('properties',{}).items()
                if k.casefold()=='bedsize'}
    context=dict(product_type=row['product_type'],copy=variant.get('content',{}).get('copy',{}))
    key=fingerprint(dict(identity=vision_identity(ai),sku=row['source_sku'],images=[d for d,_ in images],
                         task_version='attribute-inference-v2',context=context,fields=fields))
    with catalog.connect() as db:
        cached=db.execute('SELECT data_json FROM listing_vision_cache WHERE digest=?',(key,)).fetchone()
    if cached:
        return json.loads(cached[0]),dict(state='complete',cached=True,images=[d for d,_ in images],warnings=failures)
    result=ai.query('image_evidence',dict(source_sku=row['source_sku'],_images=[p for _,p in images],
        images=[d for d,_ in images],fields=fields,
        context=context,
        task='Describe visible product structure/style and legible specification labels. '
            'For bedSize, actively inspect dimension diagrams, mattress labels and unfolded sleeping-area proportions. '
            'Choose the most plausible actual provided bedSize option, even when confidence is low; '
            'use visual_inference and explain assumptions and uncertainty. Prioritize readable sleeping-area '
            'dimensions over whole-sofa or packaging dimensions. Standard US mattress sizes may guide the estimate. '
            'Non-Standard means an apparently nonstandard sleeping surface, not simply unknown dimensions. '
            'Do not invent measured dimensions or weight without a clearly readable label. '
            'Do not infer certifications, origin or product identifiers.',
        output_contract={'source_sku':row['source_sku'],'observations':[{'attribute':'field or ordinary attribute name',
            'value':'observed value','kind':'visual_inference or image_label','images':['exact image digest'],
            'source_text':'verbatim readable label for image_label, otherwise empty','reason':'中文依据和局限','confidence':'0..1'}]}))
    if result.get('source_sku')!=row['source_sku'] or not isinstance(result.get('observations'),list):
        raise ListingAIError('识图返回的 SKU 或结构不一致，未采用')
    accepted=[]
    for observation in result['observations'][:40]:
        if not isinstance(observation,dict):
            continue
        refs=observation.get('images',[]);confidence=observation.get('confidence')
        if (observation.get('kind') not in ('visual_inference','image_label') or not refs
                or not isinstance(refs,list) or any(r not in [d for d,_ in images] for r in refs)
                or type(confidence) not in (int,float) or not 0<confidence<=1 or not observation.get('reason')
                or not observation.get('attribute') or observation.get('value') is None
                or observation['kind']=='image_label' and not observation.get('source_text')):
            continue
        accepted.append(observation)
    with catalog.connect() as db:
        db.execute('INSERT INTO listing_vision_cache VALUES(?,?,?) ON CONFLICT(digest) DO NOTHING',(key,encoded(accepted),int(time.time())))
    return accepted,dict(state='complete',images=[d for d,_ in images],warnings=failures)


def stable_feed_row(db,row):
    """Use identical cached image assets in payloads, without editing the gallery."""
    import copy
    result=copy.deepcopy(row)
    def stable(url):
        if not isinstance(url,str) or not url.startswith('https://'):
            return url
        records=db.execute('SELECT DISTINCT digest FROM listing_image_evidence WHERE listing_id=? AND source_sku=? AND url_hash=?',
            (row['listing_id'],row['source_sku'],hashlib.sha256(url.encode()).hexdigest())).fetchall()
        if len(records)==1:
            return '/api/products/images/assets/'+records[0][0]
        return url
    result['visible']['mainImageUrl']=stable(row['visible'].get('mainImageUrl'))
    if isinstance(row['visible'].get('productSecondaryImageURL'),list):
        result['visible']['productSecondaryImageURL']=[stable(u) for u in row['visible']['productSecondaryImageURL']]
    return result
