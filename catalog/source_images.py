"""Download verified SKU galleries before generation; reuse content-addressed assets."""
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import HTTPException

from .database import write_lock
from .listing_ai_client import ListingAIError
from .object_storage import StorageError

PREFIX = '/api/products/images/assets/'


def cached_image(catalog, lid, sku, url):
    from .listing_vision import image_data
    with catalog.connect() as db:
        rows = db.execute('''SELECT DISTINCT digest FROM listing_image_evidence
            WHERE listing_id=? AND source_sku=? AND url_hash=?''',
            (lid, sku, hashlib.sha256(url.encode()).hexdigest())).fetchall()
    if len(rows) == 1:
        local = PREFIX + rows[0][0]
        try:
            image_data(catalog, local)
            return local
        except (ListingAIError, HTTPException, StorageError, ValueError, OSError):
            pass  # Missing/corrupt cached bytes must be collected again.
    return ''


def collect_images(catalog, lid, sku, urls):
    from .listing_vision import image_data
    candidates = list(dict.fromkeys(u for u in urls if isinstance(u, str) and u))
    selected = candidates[:32]

    def collect(url):
        try:
            cached = cached_image(catalog, lid, sku, url)
            if cached:
                return dict(source_url=url, url=cached, cached=True)
            dimensions = {}
            digest, _ = image_data(catalog, url, dimensions)
            with catalog.connect() as db:
                write_lock(db)
                db.execute('INSERT INTO listing_image_evidence VALUES(?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                           (lid, sku, hashlib.sha256(url.encode()).hexdigest(), digest,
                            dimensions['width'], dimensions['height'], int(time.time())))
            return dict(source_url=url, url=PREFIX + digest, cached=url.startswith(PREFIX))
        except (ListingAIError, HTTPException, StorageError, ValueError, OSError, httpx.HTTPError) as exc:
            if isinstance(exc, HTTPException):
                reason = str(exc.detail)
            elif isinstance(exc, httpx.TimeoutException):
                reason = '图片下载超时，请稍后重试或本地上传'
            elif isinstance(exc, httpx.HTTPError):
                reason = '图片服务器连接失败，请稍后重试或本地上传'
            elif isinstance(exc, (ListingAIError, StorageError)):
                reason = str(exc)
            else:
                reason = '图片地址或文件无效，请重新采集或本地上传'
            return dict(source_url=url, reason=reason)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(collect, selected))
    saved = [r for r in results if r.get('url')]
    failures = [dict(index=i+1, reason=r['reason']) for i, r in enumerate(results) if not r.get('url')]
    images = list(dict.fromkeys(r['url'] for r in saved))
    state = 'complete' if saved and not failures and len(candidates) <= 32 else 'partial' if saved else 'failed' if selected else 'missing'
    message = f'已保存 {len(images)} 张图片到服务器' if images else '未取得可用图片'
    if failures:
        message += f'，{len(failures)} 张失败：' + failures[0]['reason']
    if len(candidates) > 32:
        message += '；本次最多采集前 32 张，其余原图保留'
    return dict(state=state, message=message, images=images, saved=saved, failures=failures)


def collect_amazon_images(catalog, lid, sku, asin, html):
    from .images import amazon_images
    try:
        urls = amazon_images(html, asin)
    except ValueError as exc:
        return dict(state='missing', message=str(exc), images=[], saved=[], failures=[])
    return collect_images(catalog, lid, sku or '', urls)


def localize_row(row, collection):
    """Keep selection and order, replace successfully collected URLs only."""
    mapping = {r['source_url']: r['url'] for r in collection['saved']}
    visible = row['visible']
    visible['mainImageUrl'] = mapping.get(visible.get('mainImageUrl'), visible.get('mainImageUrl', ''))
    if isinstance(visible.get('productSecondaryImageURL'), list):
        visible['productSecondaryImageURL'] = list(dict.fromkeys(
            mapping.get(url, url) for url in visible['productSecondaryImageURL']))
    # Replace a failed hero with a verified image, preserving user-selected extras.
    if visible['mainImageUrl'] not in collection['images'] and collection['images']:
        visible['mainImageUrl'] = collection['images'][0]
        if not visible.get('productSecondaryImageURL'):
            visible['productSecondaryImageURL'] = collection['images'][1:8]
    if isinstance(visible.get('productSecondaryImageURL'), list):
        visible['productSecondaryImageURL'] = [u for u in visible['productSecondaryImageURL']
                                              if u != visible['mainImageUrl']]
