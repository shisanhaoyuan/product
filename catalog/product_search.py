"""Shared exact SKU/ASIN batch search validation for lists, exports and selections."""
import re


def parse_identifiers(value):
    if len(value) > 6000:
        raise ValueError('批量搜索内容不能超过 6000 个字符')
    values = list(dict.fromkeys(s.lower() for s in re.split(r'[\s,，;；、]+', value.strip()) if s))
    if len(values) > 100:
        raise ValueError('批量搜索一次最多支持 100 个不同的 SKU 或 ASIN')
    if any(len(s) > 200 for s in values):
        raise ValueError('单个 SKU 或 ASIN 不能超过 200 个字符')
    return values


def normalize_identifiers(value):
    return '\n'.join(parse_identifiers(value))


def identifier_sql(value, postgres):
    values = parse_identifiers(value)
    if not values:
        return '', []
    placeholders = ','.join('?' for _ in values)
    if postgres:
        root = 'listings.data_json::jsonb'
        fields = [f"lower({root}->>'{key}') IN ({placeholders})" for key in ('asin', 'sku', 'platform_sku')]
        variants = f"jsonb_array_elements({root}->'variants') AS batch_variant(value)"
        child = [f"lower(batch_variant.value->>'{key}') IN ({placeholders})" for key in ('asin', 'sku')]
    else:
        fields = [f"lower(json_extract(listings.data_json,'$.{key}')) IN ({placeholders})" for key in ('asin', 'sku', 'platform_sku')]
        variants = "json_each(listings.data_json,'$.variants') AS batch_variant"
        child = [f"lower(json_extract(batch_variant.value,'$.{key}')) IN ({placeholders})" for key in ('asin', 'sku')]
    fields.append(f"EXISTS (SELECT 1 FROM {variants} WHERE {' OR '.join(child)})")
    return '(' + ' OR '.join(fields) + ')', values * 5
