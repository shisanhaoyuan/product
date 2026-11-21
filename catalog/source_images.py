"""Download verified SKU galleries before generation; reuse content-addressed assets."""
import hashlib
import re
import time
from urllib.parse import urlsplit
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
    return dict(state=state, message=message, images=images, saved=saved, failures=failures,
                failed_urls=[r['source_url'] for r in results if not r.get('url')])


def collect_amazon_images(catalog, lid, sku, asin, html):
    from .images import amazon_images
    try:
        urls = amazon_images(html, asin)
    except ValueError as exc:
        return dict(state='missing', message=str(exc), images=[], saved=[], failures=[])
    return collect_images(catalog, lid, sku or '', urls)


def recover_imported_images(catalog, item, variant, urls, collection):
    """One current-ASIN gallery attempt for failed ERP images, retaining successes."""
    from .listing_vision import IMPORTED_IMAGE_HOSTS
    from .images import HumanVerificationRequired, fetch_amazon_page
    from .source_capture import collect_quote_page
    if item.get('source') != 'Amazon' or collection.get('recovery') or not collection.get('failures'):
        return collection
    asin = variant.get('asin', '')
    if not re.fullmatch(r'[A-Z0-9]{10}', asin):
        return collection
    def imported(url):
        try:
            return urlsplit(url).hostname in IMPORTED_IMAGE_HOSTS
        except ValueError:
            return False
    failed_urls = collection.get('failed_urls')
    if failed_urls is None:
        candidates = list(dict.fromkeys(u for u in urls if isinstance(u, str) and u))
        failed_urls = [candidates[f['index']-1] for f in collection['failures']
                       if 0 < f.get('index', 0) <= len(candidates)]
    if not any(imported(u) for u in failed_urls):
        return collection
    try:
        html, _, _ = collect_quote_page(asin, fetch_amazon_page)
        recovered = collect_amazon_images(catalog, item['listing_id'], variant['sku'], asin, html)
        images = list(dict.fromkeys(collection['images'] + recovered['images']))
        return collection | dict(
            state='partial' if images else 'failed', images=images,
            saved=collection['saved'] + recovered['saved'], recovery='amazon_current_asin',
            recovered_images=recovered['images'], failed_urls=failed_urls + recovered.get('failed_urls', []),
            recovery_failures=recovered['failures'],
            message=f'原图片已保存 {len(collection["images"])} 张；当前 ASIN 补采保存 {len(recovered["images"])} 张。'
                    + (f'共 {len(images)} 张可用，失效旧链接已从本次刊登选择中移除。' if images else recovered['message']))
    except (ValueError, OSError, ListingAIError) as exc:
        message = 'Amazon 要求人机验证，请完成验证或上传当前 SKU 图片' if isinstance(exc, HumanVerificationRequired) else str(exc)[:200]
        return collection | dict(recovery='amazon_current_asin', message=collection['message']+'；原图补采失败：'+message)


def reuse_verified_source_gallery(collection, source_capture, asin):
    """Replace failed imported CDN images with the already saved same-ASIN gallery."""
    from .listing_vision import IMPORTED_IMAGE_HOSTS
    if collection.get('recovery') or not source_capture or source_capture.get('asin') != asin:
        return collection
    failed_urls = collection.get('failed_urls') or []
    def imported(url):
        try:
            return urlsplit(url).hostname in IMPORTED_IMAGE_HOSTS
        except (TypeError, ValueError):
            return False
    if not any(imported(url) for url in failed_urls):
        return collection
    gallery = source_capture.get('image_collection') or {}
    saved = [row for row in gallery.get('saved', [])
             if isinstance(row, dict) and isinstance(row.get('url'), str)
             and re.fullmatch(re.escape(PREFIX) + r'[a-f0-9]{64}', row['url'])]
    saved_urls = {row['url'] for row in saved}
    gallery_images = [url for url in gallery.get('images', [])
                      if isinstance(url, str) and url in saved_urls]
    if not gallery_images:
        return collection
    images = list(dict.fromkeys([*collection.get('images', []), *gallery_images]))
    existing_saved = {(row.get('source_url'), row.get('url')) for row in collection.get('saved', [])
                      if isinstance(row, dict)}
    recovered_saved = [row for row in saved if (row.get('source_url'), row['url']) not in existing_saved]
    return collection | dict(
        state='partial' if collection.get('failures') else 'complete',
        images=images,
        saved=[*collection.get('saved', []), *recovered_saved],
        recovery='saved_current_asin_gallery',
        recovered_images=gallery_images,
        message=(f'已复用来源采集保存的同 ASIN 图片 {len(gallery_images)} 张，替换失效 ERP 图片；'
                 f'目前共 {len(images)} 张服务器图片可用。'),
    )


def localize_row(row, collection):
    """Keep selected successes; omit known failed URLs and fill with verified recovery."""
    mapping = {r['source_url']: r['url'] for r in collection['saved']}
    visible = row['visible']
    original_count = len(visible.get('productSecondaryImageURL', [])) + bool(visible.get('mainImageUrl'))
    failed = set(collection.get('failed_urls', []))
    visible['mainImageUrl'] = mapping.get(visible.get('mainImageUrl'), visible.get('mainImageUrl', ''))
    if isinstance(visible.get('productSecondaryImageURL'), list):
        visible['productSecondaryImageURL'] = list(dict.fromkeys(
            mapping.get(url, url) for url in visible['productSecondaryImageURL'] if url not in failed))
    if visible['mainImageUrl'] in failed:
        visible['mainImageUrl'] = ''
    # Replace a failed hero with a verified image, preserving user-selected extras.
    if visible['mainImageUrl'] not in collection['images'] and collection['images']:
        visible['mainImageUrl'] = collection['images'][0]
        if not visible.get('productSecondaryImageURL'):
            visible['productSecondaryImageURL'] = collection['images'][1:8]
    if isinstance(visible.get('productSecondaryImageURL'), list):
        visible['productSecondaryImageURL'] = [u for u in visible['productSecondaryImageURL']
                                              if u != visible['mainImageUrl']]
    if collection.get('recovered_images'):
        selected = list(dict.fromkeys([visible['mainImageUrl'], *visible.get('productSecondaryImageURL', []),
                                       *collection['recovered_images']]))
        selected = [u for u in selected if u][:max(8, original_count)]
        visible['mainImageUrl'] = selected[0] if selected else ''
        visible['productSecondaryImageURL'] = selected[1:]
