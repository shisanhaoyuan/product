"""Read explicit ERP cells for fields added after the initial catalog import."""
from urllib.parse import urlparse


def with_variant_fields(variant):
    result = dict(variant)
    cells = variant.get('raw_cells', {})
    merged = {}
    for cell in cells.values():
        if isinstance(cell, dict) and isinstance(cell.get('value'), dict):
            merged.update(cell['value'])
    for field, key in [('product_id_type', 'product id type'),
                       ('swatch_attribute', 'sva'), ('shipping_weight', 'shippingWeight')]:
        value = merged.get(key, '')
        result.setdefault(field, value if isinstance(value, str) else '')
    images = merged.get('siu', {})
    urls = list(images.values()) if isinstance(images, dict) else []
    swatch = ''
    for url in urls:
        if not isinstance(url, str):
            continue
        try:
            parsed = urlparse(url)
            if parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username and not parsed.password:
                swatch = url
                break
        except ValueError:
            continue
    result.setdefault('swatch_image', swatch)
    return result
