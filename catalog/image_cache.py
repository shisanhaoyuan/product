"""Persist imported CDN images on first display, bound to the current product."""
import hashlib
import threading
from urllib.parse import urlsplit

from fastapi import HTTPException

from .listing_vision import IMPORTED_IMAGE_HOSTS
from .source_images import collect_images

_SLOTS = threading.BoundedSemaphore(4)
_LOCKS = [threading.Lock() for _ in range(32)]


def cache_product_image(catalog, lid, url):
    product = catalog.get(lid)
    if not product:
        raise HTTPException(404, '商品不存在')
    try:
        parsed = urlsplit(url)
        allowed = (parsed.scheme == 'https' and parsed.hostname in IMPORTED_IMAGE_HOSTS
                   and not parsed.username and not parsed.password and parsed.port in (None, 443))
    except ValueError:
        allowed = False
    if not allowed or product['source'] != 'Amazon':
        raise HTTPException(422, '仅缓存当前亚马逊商品已记录的导入图片')
    scopes = [('', product['images']), *((v['sku'], v['images']) for v in product['variants'])]
    sku = next((sku for sku, images in scopes if url in images), None)
    if sku is None:
        raise HTTPException(404, '图片不属于当前商品')
    key = hashlib.sha256((lid + '\0' + url).encode()).digest()
    # Bound network concurrency and coalesce simultaneous hero/thumbnail requests.
    with _LOCKS[key[0] % len(_LOCKS)]:
        with _SLOTS:
            collection = collect_images(catalog, lid, sku, [url])
    if not collection['images']:
        raise HTTPException(502, collection['message'])
    return collection['images'][0].rsplit('/', 1)[-1]
