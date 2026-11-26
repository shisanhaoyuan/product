"""Deterministic brand prefix for listing copy; never changes source records."""
import html
import json
import re

BRAND_POLICY = 'brand_consistency_v2'
COPY_FIELDS = ('productName', 'shortDescription', 'longDescription', 'keyFeatures')


def source_brands(source):
    """Read explicitly labeled brands, including Amazon/ERP retained attributes.

    Do not guess brands from titles, sellers, manufacturers or arbitrary prose.
    """
    values = []
    labels = {'brand', 'brandname', '品牌', '品牌名称'}
    def name(key):
        return re.sub(r'[\W_]+', '', str(key)).casefold()
    def unpack(value, depth=0):
        if depth > 12:
            return
        if isinstance(value, str):
            value = html.unescape(value).strip()
            if value.startswith(('[', '{')):
                try:
                    unpack(json.loads(value), depth+1)
                except (ValueError, TypeError):
                    pass
            elif value and value.casefold() not in ('n/a', 'unknown', 'none', '未填写'):
                values.append(value)
        elif isinstance(value, list):
            for item in value:
                unpack(item, depth+1)
        elif isinstance(value, dict):
            for key in ('val', 'value', 'variant_value', 'en'):
                if key in value:
                    unpack(value[key], depth+1)
    def walk(value, depth=0):
        if depth > 16:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if name(key) in labels:
                    unpack(child)
                else:
                    walk(child, depth+1)
            if name(value.get('name') or value.get('label') or value.get('AttributeName') or '') in labels:
                unpack(value.get('value', value.get('val', value.get('AttributeValue'))))
        elif isinstance(value, list):
            for child in value:
                walk(child, depth+1)
    walk(source)
    return list({value.casefold(): value for value in values}.values())


def brand_pattern(brand):
    pattern = re.escape(brand).replace(r'\ ', r'\s+')
    return ((r'(?<!\w)' if brand[0].isascii() and brand[0].isalnum() else '') + pattern
            + (r'(?!\w)' if brand[-1].isascii() and brand[-1].isalnum() else ''))


def normalize_copy_brand(visible, target, brands):
    from .listing_templates import replace_brand
    visible['productName'] = branded_title(visible.get('productName', ''), target, brands)
    for field in COPY_FIELDS[1:]:
        if field in visible:
            visible[field] = replace_brand(visible[field], brands, target)


def brand_errors(visible, target, brands):
    """Final guard catches residual markup/AI copy that normalization could not clean."""
    errors = []
    if visible.get('brand') != target:
        errors.append(dict(path='visible.brand', message='品牌与本批次目标品牌不一致，请核对'))
    def check(value, path):
        if isinstance(value, list):
            for index, item in enumerate(value):
                check(item, f'{path}.{index}')
        elif isinstance(value, dict):
            for key, item in value.items():
                check(item, f'{path}.{key}')
        elif isinstance(value, str):
            plain = html.unescape(re.sub(r'<[^>]*>', '', value))
            if target:
                plain = re.sub(brand_pattern(target), '', plain, flags=re.I)
            for brand in brands:
                if brand and brand.casefold() != target.casefold() and re.search(brand_pattern(brand), plain, re.I):
                    errors.append(dict(path=path, message=f'文案仍含来源品牌「{brand}」，目标店铺品牌为「{target}」，禁止提交'))
    for field in COPY_FIELDS:
        check(visible.get(field), 'visible.'+field)
    title = html.unescape(re.sub(r'<[^>]*>', '', str(visible.get('productName', '')))).strip()
    if target and not re.match(brand_pattern(target), title, re.I):
        errors.append(dict(path='visible.productName', message='商品标题必须以目标店铺品牌开头'))
    return errors


def branded_title(title, brand, brands=()):
    """Remove known brands as whole tokens, then prefix the target exactly once.

    Unknown words are retained: guessing that a title's first word is a brand
    could erase actual product facts. No truncation of dimensions or words.
    """
    if not isinstance(title, str) or not isinstance(brand, str) or not brand.strip():
        return title
    body = html.unescape(re.sub(r'<[^>]*>', ' ', title)).strip()
    brand = brand.strip()
    for known in sorted({brand, *(v.strip() for v in brands if isinstance(v, str) and v.strip())}, key=len, reverse=True):
        pattern = re.escape(known).replace(r'\ ', r'\s+')
        # Letter/digit boundaries protect e.g. Oak in Oakwood and Apple in Pineapple.
        pattern = (r'(?<!\w)' if known[0].isascii() and known[0].isalnum() else '') + pattern
        pattern += r'(?!\w)' if known[-1].isascii() and known[-1].isalnum() else ''
        body = re.sub(pattern + r'[™®]?', '', body, flags=re.I)
    body = re.sub(r'\(\s*\)|\[\s*\]', '', body)
    body = re.sub(r'([-|:;·–—])\s*[-|:;·–—](?=\s|$)', r'\1', body)
    body = re.sub(r'\s+', ' ', body).strip(' \t\r\n-|:;,·–—™®')
    return f'{brand} {body}' if body else ''
