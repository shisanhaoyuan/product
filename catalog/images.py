"""Local image assets and explicitly requested, ASIN-bound Amazon image discovery."""
import ast
import hashlib
import io
import json
import re
import warnings
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

MAX_UPLOAD = 8 * 1024 * 1024


def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS image_assets(
        digest TEXT PRIMARY KEY, content BLOB NOT NULL, mime TEXT NOT NULL,
        created_at TEXT NOT NULL, actor_id INTEGER);
        INSERT OR IGNORE INTO migrations VALUES(6);''')


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


def amazon_images(html, asin):
    if 'validateCaptcha' in html or 'Type the characters you see' in html or 'api-services-support@amazon.com' in html:
        raise ValueError('Amazon 要求人机验证，请稍后重试或使用图片链接导入')
    page = ProductHTML()
    page.feed(html)
    if page.asins != {asin}:
        raise ValueError('无法核验网页当前子 ASIN，未采用页面图片')
    candidates = []
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
                        candidates.append(row.get('hiRes') or row.get('large') or '')
        except (ValueError, TypeError, SyntaxError):
            pass
    if not candidates:
        if page.landing.get('data-old-hires'):
            candidates.append(page.landing['data-old-hires'])
        else:
            try:
                dynamic = json.loads(page.landing.get('data-a-dynamic-image', '{}'))
                if isinstance(dynamic, dict):
                    candidates.extend(list(dynamic)[:1])
            except ValueError:
                pass
    result = []
    for url in candidates:
        if not isinstance(url, str):
            continue
        parsed = urlparse(url)
        if (parsed.scheme == 'https' and parsed.hostname in {'m.media-amazon.com', 'images-na.ssl-images-amazon.com'}
                and parsed.path.startswith('/images/I/') and not parsed.username and not parsed.password):
            if url not in result:
                result.append(url)
    if not result:
        raise ValueError('页面未返回可核验的当前商品图片，原图片未改变')
    return result[:100]


def fetch_amazon_images(asin):
    url = f'https://www.amazon.com/dp/{asin}?th=1&psc=1'
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=8), follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', url, headers={'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.9'}) as response:
                if response.status_code != 200:
                    raise ValueError(f'Amazon 暂未返回商品页面（HTTP {response.status_code}），请稍后重试')
                raw = bytearray()
                for part in response.iter_bytes():
                    raw.extend(part)
                    if len(raw) > 6 * 1024 * 1024:
                        raise ValueError('来源页面超出大小限制')
        return {'asin': asin, 'source_url': url, 'images': amazon_images(raw.decode('utf-8', errors='replace'), asin),
                'fetched_at': datetime.now(timezone.utc).isoformat()}
    except httpx.HTTPError as exc:
        raise ValueError('无法连接 Amazon，请检查服务器网络后重试；原图片未改变') from exc


class CaptureInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    variant_sku: str | None = Field(default=None, min_length=1, max_length=200)


def image_routes(catalog):
    router = APIRouter(prefix='/api/products')

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
    def asset(digest: str):
        if not re.fullmatch('[a-f0-9]{64}', digest):
            raise HTTPException(404)
        with catalog.connect() as db:
            row = db.execute('SELECT content,mime FROM image_assets WHERE digest=?', (digest,)).fetchone()
        if not row:
            raise HTTPException(404, '图片不存在')
        return Response(row['content'], media_type=row['mime'])

    @router.post('/{lid}/images/upload')
    async def upload(lid: str, request: Request, variant_sku: str | None = None):
        target(lid, variant_sku)
        raw = bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw) > MAX_UPLOAD:
                raise HTTPException(413, '单张图片不能超过 8 MB')
        content, mime = normalize_image(bytes(raw))
        digest = hashlib.sha256(content).hexdigest()
        user = getattr(request.state, 'user', None)
        with catalog.connect() as db:
            db.execute('INSERT OR IGNORE INTO image_assets VALUES(?,?,?,?,?)',
                       (digest, content, mime, datetime.now(timezone.utc).isoformat(), user['id'] if user else None))
        return {'url': '/api/products/images/assets/' + digest}

    @router.post('/{lid}/images/capture')
    def capture(lid: str, data: CaptureInput):
        p, v = target(lid, data.variant_sku)
        if p['source'].casefold() != 'amazon' or not re.fullmatch('[A-Z0-9]{10}', v['asin']):
            raise HTTPException(422, '当前商品没有明确的 Amazon ASIN，不能推测抓取')
        try:
            return fetch_amazon_images(v['asin'])
        except ValueError as exc:
            raise HTTPException(502, str(exc))

    return router
