"""Local image assets and explicitly requested, ASIN-bound Amazon image discovery."""
import ast
import hashlib
import io
import json
import re
import warnings
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from .object_storage import StorageError, save_metadata

MAX_UPLOAD = 8 * 1024 * 1024


class HumanVerificationRequired(ValueError):
    pass


class ImageGalleryUnavailable(ValueError):
    """Identity matched, but the static response has no usable current gallery."""
    pass


def needs_verification(html):
    return any(marker in html for marker in (
        'validateCaptcha', 'Type the characters you see',
        'api-services-support@amazon.com', 'captcha/verify', 'Robot Check',
    ))


def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS image_assets(
        digest TEXT PRIMARY KEY, content BLOB NOT NULL, mime TEXT NOT NULL,
        created_at TEXT NOT NULL, actor_id INTEGER);
        CREATE TABLE IF NOT EXISTS image_objects(
        digest TEXT PRIMARY KEY,object_key TEXT NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,
        created_at TEXT NOT NULL,actor_id INTEGER,bucket TEXT NOT NULL,endpoint TEXT NOT NULL,
        backend TEXT NOT NULL DEFAULT 'oss');
        INSERT OR IGNORE INTO migrations VALUES(6);''')
    if 'backend' not in {row[1] for row in db.execute('PRAGMA table_info(image_objects)')}:
        db.execute("ALTER TABLE image_objects ADD COLUMN backend TEXT NOT NULL DEFAULT 'oss'")


def normalize_image(raw):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in {'JPEG', 'PNG', 'WEBP'} or source.width * source.height > 25000000:
                    raise ValueError('只支持 JPG、PNG、WebP，最多 2500 万像素')
                source.load()
                image = ImageOps.exif_transpose(source)
                alpha = 'A' in image.getbands() or 'transparency' in image.info
                image = image.convert('RGBA' if alpha else 'RGB')
                image.info.clear()
                out = io.BytesIO()
                image.save(out, format='PNG' if alpha else 'JPEG', **({} if alpha else {'quality': 92}))
                result = out.getvalue()
                if len(result) > MAX_UPLOAD:
                    raise ValueError('处理后的图片超过 8 MB，请先缩小图片')
                return result, 'image/png' if alpha else 'image/jpeg'
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(422, '图片无效或尺寸过大，仅支持 JPG、PNG、WebP（每张 8 MB）') from exc


class ProductHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.asins = set()
        self.landing = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'input' and a.get('id') == 'ASIN' and a.get('value'):
            self.asins.add(a['value'].upper())
        if tag == 'img' and a.get('id') == 'landingImage':
            self.landing = a


def amazon_image_url(value):
    """Normalize Amazon's experiment CDN prefix without changing image identity."""
    if not isinstance(value, str):
        return ''
    try:
        url = urlparse(value)
        if (url.scheme != 'https' or url.hostname not in
                {'m.media-amazon.com', 'images-na.ssl-images-amazon.com'}
                or url.username or url.password or url.port not in (None, 443)):
            return ''
        path = re.sub(r'^/images/W/[A-Za-z0-9_-]+/images/I/', '/images/I/', url.path)
        if not re.fullmatch(r'/images/I/[^/]+', path):
            return ''
        return url._replace(path=path, query='', fragment='').geturl()
    except ValueError:
        return ''


def amazon_image_identity(url):
    # Size/quality transformations of the same asset are not extra photos.
    return urlparse(url).path.rsplit('/', 1)[-1].split('._', 1)[0]


def amazon_images(html, asin):
    if needs_verification(html):
        raise HumanVerificationRequired('Amazon 要求人机验证，请在打开的浏览器页面完成验证')
    page = ProductHTML()
    page.feed(html)
    if page.asins != {asin}:
        raise ValueError('无法核验网页当前子 ASIN，未采用页面图片')
    candidates = []
    aliases = {}
    # Only the current gallery's initial images; never scan recommendations or all variants.
    match = re.search(r'''["']colorImages["']\s*:\s*\{\s*["']initial["']\s*:\s*''', html)
    if match:
        try:
            payload = html[match.end():]
            # Amazon also wraps the same current gallery in a quoted JSON string.
            # Decode literals only; never execute page JavaScript.
            wrapped = re.match(r'''A\.\$\.parseJSON\(\s*('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")\s*\)''', payload)
            if wrapped:
                gallery = json.loads(ast.literal_eval(wrapped.group(1)))
            else:
                gallery, _ = json.JSONDecoder().raw_decode(payload)
            if isinstance(gallery, list):
                for row in gallery:
                    if isinstance(row, dict):
                        urls = [amazon_image_url(row.get(key)) for key in ('hiRes', 'large')]
                        preferred = next((url for url in urls if url), '')
                        if preferred:
                            candidates.append(preferred)
                            # Amazon uses different physical IDs for some thumbnails.
                            # Map only aliases explicitly linked by this current gallery.
                            for value in [row.get('hiRes'), row.get('large'), row.get('thumb')]:
                                normalized = amazon_image_url(value)
                                if normalized:
                                    aliases[amazon_image_identity(normalized)] = preferred
        except (ValueError, TypeError, SyntaxError):
            pass
    result = []
    seen = set()

    def add(url):
        url = aliases.get(amazon_image_identity(url), url)
        identity = amazon_image_identity(url)
        if identity not in seen:
            seen.add(identity)
            result.append(url)

    for url in candidates:
        add(url)
    from .source_capture import Document

    doc = Document(html).root
    # A plugin-produced current gallery explicitly carries the verified ASIN.
    # Do this check even when the script gallery returned URLs, so a mismatched
    # page cannot be partially accepted from one of its other representations.
    galleries = doc.ids('jiwu-image-gallery')
    if any(node.attrs.get('data-asin') != asin for node in galleries):
        raise ValueError('图片图库 ASIN 与当前子商品不一致，原图片未改变')
    # The static colorImages data is often incomplete (e.g. only the first three
    # images). Merge it with the browser gallery instead of treating either
    # representation as an exclusive fallback.
    nodes = [child for section in galleries for child in section.nodes() if child.tag == 'img']
    if not nodes:
        # Raw HTML/manual HTTP capture: only the current product image block,
        # never a page-wide img scan (recommendations/variant swatches).
        for node in doc.nodes():
            if node.tag != 'img':
                continue
            parent = node
            within = False
            excluded = False
            while parent.parent:
                ident = parent.attrs.get('id', '')
                if re.search(r'recommend|sponsored|twister|^variation_', ident, re.I):
                    excluded = True
                if 'videoThumbnail' in parent.attrs.get('class', '').split():
                    excluded = True
                if ident in ('landingImage', 'imgBlkFront', 'ebooksImgBlkFront', 'imgTagWrapperId', 'imageBlock'):
                    within = True
                parent = parent.parent
            if within and not excluded:
                nodes.append(node)
    for node in nodes:
        attrs = node.attrs
        dynamic = []
        try:
            values = json.loads(attrs.get('data-a-dynamic-image', '{}'))
            if isinstance(values, dict):

                def area(pair):
                    dimensions = pair[1]
                    if (
                        isinstance(dimensions, list)
                        and len(dimensions) == 2
                        and all(isinstance(value, (int, float)) for value in dimensions)
                    ):
                        return dimensions[0] * dimensions[1]
                    return 0

                dynamic = [url for url, _ in sorted(values.items(), key=area, reverse=True)]
        except (ValueError, TypeError):
            pass
        urls = [attrs.get('data-old-hires'), *dynamic, attrs.get('src'), attrs.get('data-src')]
        url = next((value for candidate in urls if (value := amazon_image_url(candidate))), '')
        if url:
            add(url)
    if not result:
        raise ImageGalleryUnavailable('页面未返回可核验的当前商品图片，原图片未改变')
    return result[:100]


def fetch_amazon_page(asin):
    url = f'https://www.amazon.com/dp/{asin}?th=1&psc=1'
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=8), follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', url, headers={'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.9'}) as response:
                raw = bytearray()
                for part in response.iter_bytes():
                    raw.extend(part)
                    if len(raw) > 6 * 1024 * 1024:
                        raise ValueError('来源页面超出大小限制')
                html = raw.decode('utf-8', errors='replace')
                if needs_verification(html) or 'validateCaptcha' in response.headers.get('location', ''):
                    raise HumanVerificationRequired('Amazon 要求人机验证')
                if response.status_code != 200:
                    raise ValueError(f'Amazon 暂未返回商品页面（HTTP {response.status_code}），请稍后重试')
        return html
    except httpx.HTTPError as exc:
        raise ValueError('无法连接 Amazon，请检查服务器网络后重试') from exc


def fetch_amazon_images(asin):
    return {'asin': asin, 'source_url': f'https://www.amazon.com/dp/{asin}?th=1&psc=1',
            'images': amazon_images(fetch_amazon_page(asin), asin),
            'fetched_at': datetime.now(timezone.utc).isoformat()}


class CaptureInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    variant_sku: str | None = Field(default=None, min_length=1, max_length=200)


class BrowserCaptureInput(CaptureInput):
    source_url: str = Field(max_length=2000)
    html: str = Field(min_length=1, max_length=6 * 1024 * 1024)


def image_routes(catalog, storage):
    router = APIRouter(prefix='/api/products')

    @router.get('/images/helper-info')
    def helper_info():
        root = Path(__file__).resolve().parent.parent / 'browser-helper'
        try:
            manifest = json.loads((root / 'manifest.json').read_text())
            result = {'name': manifest['name'], 'version': manifest['version']}
        except (OSError, ValueError, KeyError):
            raise HTTPException(503, '采集插件信息缺失，请检查部署') from None
        return Response(json.dumps(result, ensure_ascii=False), media_type='application/json',
                        headers={'Cache-Control': 'no-store'})

    @router.get('/images/helper.zip')
    def helper_download():
        root = Path(__file__).resolve().parent.parent / 'browser-helper'
        files = ('manifest.json', 'popup.html', 'popup.js', 'bridge.js', 'worker.js', 'location-state.js', 'product.js', 'offers.js', 'gallery.js', 'amazon.js')
        if not all((root / name).is_file() for name in files):
            raise HTTPException(503, '采集插件文件缺失，请检查部署')
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in files:
                archive.write(root / name, name)
        return Response(output.getvalue(), media_type='application/zip',
                        headers={'Content-Disposition': 'attachment; filename="capture-helper.zip"',
                                 'Cache-Control': 'no-store'})

    def target(lid, sku):
        p = catalog.get(lid)
        if not p:
            raise HTTPException(404, '商品不存在')
        if sku is None:
            return p, p
        v = next((v for v in p['variants'] if v['sku'] == sku), None)
        if v is None:
            raise HTTPException(409, '子 SKU 不属于此商品，请重新打开')
        return p, v

    @router.get('/images/assets/{digest}')
    def asset(digest: str, request: Request):
        if not re.fullmatch('[a-f0-9]{64}', digest):
            raise HTTPException(404)
        with catalog.connect() as db:
            stored = db.execute('SELECT * FROM image_objects WHERE digest=?', (digest,)).fetchone()
            row = None if catalog.postgres else db.execute('SELECT mime FROM image_assets WHERE digest=?', (digest,)).fetchone()
        if not stored and not row:
            raise HTTPException(404, '图片不存在')
        if stored and storage is None:
            raise HTTPException(503, '请配置此图片使用的存储后端')
        headers = {'Cache-Control': 'private, max-age=31536000, immutable',
                   'ETag': f'"{digest}"', 'Vary': 'Cookie'}
        # Authentication still runs before conditional requests. Content hashes
        # change when the image changes; JSON and failed image responses stay uncached.
        tags = request.headers.get('if-none-match', '').split(',')
        if any(tag.strip().removeprefix('W/') in ('*', headers['ETag']) for tag in tags):
            request.state.cacheable_product_image = True
            return Response(status_code=304, headers=headers)
        if stored:
            try:
                content = storage.get(stored)
            except StorageError as exc:
                raise HTTPException(503, str(exc)) from None
        else:
            with catalog.connect() as db:
                content = db.execute('SELECT content FROM image_assets WHERE digest=?', (digest,)).fetchone()[0]
        request.state.cacheable_product_image = True
        return Response(content, media_type=(stored or row)['mime'], headers=headers)

    @router.get('/{lid}/images/cached')
    def cached_product_image(lid: str, url: str, request: Request):
        from .image_cache import cache_product_image
        response = asset(cache_product_image(catalog, lid, url), request)
        # A supplier URL is not content-addressed. Periodically recheck its
        # product association while repeat navigation uses the browser cache.
        response.headers['Cache-Control'] = 'private, max-age=86400'
        return response

    @router.post('/{lid}/images/upload')
    async def upload(lid: str, request: Request, variant_sku: str | None = None):
        await run_in_threadpool(target, lid, variant_sku)
        raw = bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw) > MAX_UPLOAD:
                raise HTTPException(413, '单张图片不能超过 8 MB')
        content, mime = await run_in_threadpool(normalize_image, bytes(raw))
        digest = hashlib.sha256(content).hexdigest()
        user = getattr(request.state, 'user', None)
        def persist():
            actor_id = user['id'] if user else None
            if storage is not None:
                metadata = storage.put(digest, content, mime)
                with catalog.connect() as db:
                    save_metadata(db, metadata, actor_id)
            else:
                with catalog.connect() as db:
                    db.execute('INSERT OR IGNORE INTO image_assets VALUES(?,?,?,?,?)',
                               (digest,content,mime,datetime.now(timezone.utc).isoformat(),actor_id))
        try:
            await run_in_threadpool(persist)
        except StorageError as exc:
            raise HTTPException(503, str(exc)) from None
        return {'url': '/api/products/images/assets/' + digest}

    @router.post('/{lid}/images/capture')
    def capture(lid: str, data: CaptureInput):
        p, v = target(lid, data.variant_sku)
        if p['source'].casefold() != 'amazon' or not re.fullmatch('[A-Z0-9]{10}', v['asin']):
            raise HTTPException(422, '当前商品没有明确的 Amazon ASIN，不能推测抓取')
        try:
            result = fetch_amazon_images(v['asin'])
            from .source_images import collect_images
            collection = collect_images(catalog, lid, data.variant_sku or '', result['images'])
            result.update(images=collection['images'], image_collection=collection)
            if not result['images']:
                raise ValueError(collection['message'])
            return result
        except ImageGalleryUnavailable as exc:
            return {
                'status': 'browser_required',
                'reason': str(exc),
                'asin': v['asin'],
                'source_url': f"https://www.amazon.com/dp/{v['asin']}?th=1&psc=1",
            }
        except HumanVerificationRequired:
            return {'status': 'verification_required', 'asin': v['asin'],
                    'source_url': f"https://www.amazon.com/dp/{v['asin']}?th=1&psc=1"}
        except ValueError as exc:
            raise HTTPException(502, str(exc))

    @router.post('/{lid}/images/capture/browser')
    def browser_capture(lid: str, data: BrowserCaptureInput):
        p, v = target(lid, data.variant_sku)
        try:
            url = urlparse(data.source_url)
        except ValueError:
            raise HTTPException(422, '浏览器来源链接无效') from None
        if (p['source'].casefold() != 'amazon' or not re.fullmatch('[A-Z0-9]{10}', v['asin'])
                or url.scheme != 'https' or url.hostname != 'www.amazon.com'
                or url.netloc not in {'www.amazon.com', 'www.amazon.com:443'}
                or not re.fullmatch(r'/(?:[^/]+/)?dp/' + re.escape(v['asin']) + r'/?', url.path)):
            raise HTTPException(422, '浏览器页面与当前商品 ASIN 不匹配')
        try:
            images = amazon_images(data.html, v['asin'])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        from .source_images import collect_images
        collection = collect_images(catalog, lid, data.variant_sku or '', images)
        if not collection['images']:
            raise HTTPException(502, collection['message'])
        return {'asin': v['asin'], 'source_url': data.source_url, 'images': collection['images'], 'image_collection': collection,
                'fetched_at': datetime.now(timezone.utc).isoformat()}

    return router
