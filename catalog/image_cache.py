"""Persist trusted supplier images on first display, bound to the current product."""
import hashlib
import threading
from urllib.parse import urlsplit

from fastapi import HTTPException

from .listing_vision import IMPORTED_IMAGE_HOSTS
from .source_images import collect_images

SUPPLIER_IMAGE_HOSTS = ('media-amazon.com', 'ssl-images-amazon.com', 'gigab2b.cn',
                        'gigab2b.com', 'saleyee.com', 'saleyee.cn')

_SLOTS = threading.BoundedSemaphore(4)
_LOCKS = [threading.Lock() for _ in range(32)]


def cache_product_image(catalog, lid, url):
    product = catalog.get(lid)
    if not product:
        raise HTTPException(404, '商品不存在')
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        allowed = (parsed.scheme == 'https'
                   and (host in IMPORTED_IMAGE_HOSTS or any(
                       host == h or host.endswith('.' + h) for h in SUPPLIER_IMAGE_HOSTS))
                   and not parsed.username and not parsed.password and parsed.port in (None, 443))
    except ValueError:
        allowed = False
    if not allowed:
        raise HTTPException(422, '仅缓存当前商品已记录的可信供应商图片')
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
