"""Port digit-emperor-listing's managed-dimension rules to actual API schemas.

Only source attribute text is remapped; it is never a newly inferred product fact.
Local semantic ranking is the Skill's authorized non-AI fallback. No model call
or network operation runs inside a document save transaction.
"""
import copy
import re
from difflib import SequenceMatcher

CUSTOM = {'color', 'size', 'pattern', 'finish', 'theme', 'shape',
          'snaredrumsize', 'drumshellcolor', 'framecolor'}
FORBIDDEN = {'multipackquantity'}
SIGNALS = {
    'color': r'colour|color|black|white|red|blue|green|pink|grey|gray|brown|beige|gold',
    'size': r'size|height|width|length|diameter|small|medium|large|inch|feet|\bft\b|\bcm\b|\bmm\b|[0-9][.0-9]*["″]',
    'pattern': r'pattern|strip|floral|print|dot|check|plaid',
    'finish': r'finish|matte|gloss|polish|brushed|satin|texture',
    'theme': r'theme|christmas|halloween|holiday|animal|character',
    'shape': r'shape|round|square|rectang|oval|triangle|hexagon',
}
# Identity, commerce and prose are not variant dimensions.
EXCLUDED = {'sku', 'asin', 'ean', 'upc', 'gtin', 'brand', 'seller', 'sellername',
            'price', 'stock', 'quantity', 'inventory', 'description', 'title',
            'productname', 'manufacturer', 'modelnumber', 'partnumber', 'mpn', 'url', 'sourceurl'}


def name(value):
    from .listing_variants import token
    key = token(value)
    return {'colour': 'color', 'basecolor': 'color', 'colorname': 'color', '颜色': 'color',
            'sizename': 'size', '尺寸': 'size', '图案': 'pattern', '形状': 'shape',
            '饰面': 'finish'}.get(key, key)


def text_value(value):
    if isinstance(value, dict) and set(value) == {'variant_value'}:
        value = value['variant_value']
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value).strip()
        return text if text and text.casefold() not in {'n/a', 'unknown', 'none', 'null', '未采集', '未知', '待核验'} else None
    return None


def custom_field(key, shape):
    # ERP free-text options do not imply that the API field accepts free text.
    if name(key) not in CUSTOM and name(shape.get('title', '')) not in CUSTOM:
        return False
    scalar = shape.get('items', {}) if shape.get('type') == 'array' else shape
    restricted = {'enum', 'const', '$ref', 'oneOf', 'anyOf', 'allOf', 'not', 'if', 'pattern'}
    return scalar.get('type') == 'string' and not restricted.intersection(shape) and not restricted.intersection(scalar)


def adapt(text, shape):
    from .listing_variants import valid_attribute
    value = text
    if shape.get('type') == 'array':
        converted = adapt(text, shape.get('items', {}))
        value = [converted] if converted is not None else None
    elif shape.get('type') in ('integer', 'number'):
        # Preserve descriptive units: never coerce "2 Pack" or "24 Inch" to a count.
        if not re.fullmatch(r'\d+(?:\.\d+)?', text):
            return None
        number = float(text)
        if shape['type'] == 'integer' and not number.is_integer():
            return None
        value = int(number) if shape['type'] == 'integer' else number
    elif 'enum' in shape:
        value = next((v for v in shape['enum'] if str(v).casefold() == text.casefold()), None)
    return value if value is not None and valid_attribute(shape, value) else None


def merge(base, extra):
    parts = [p for p in base.split('**') if p]
    for part in extra.split('**'):
        if part and part not in parts:
            parts.append(part)
    return '**'.join(parts)


def rank(source, values, candidates, props):
    text = ' '.join([source, *values]).casefold()
    evidence = []
    for key in candidates:
        label = name(key) if name(key) in CUSTOM else name(props[key].get('title', key))
        family = 'color' if label.endswith('color') else 'size' if label.endswith('size') else label
        hits = sorted(set(re.findall(SIGNALS.get(family, r'(?!)'), text)))
        overlap = SequenceMatcher(None, name(source), label).ratio()
        evidence.append(dict(target=key, signals=hits, name_similarity=overlap))
    evidence.sort(key=lambda e: (-len(e['signals']), -e['name_similarity'], e['target']))
    return evidence


def mapped_source(rows, content, allowed, props):
    """Propose complete group values, returning explicit user-rule provenance.

    Reserve all exact names before substituting any unsupported column. Map by
    platform SKU, never row order, and merge overflow into a selected custom field.
    Empty/missing/ambiguous columns cannot manufacture differences.
    """
    from .listing_variants import value_key
    by_sku = {}
    for row in rows:
        attrs = content.get(row['platform_sku'], {}).get('attributes') or {}
        columns = {}
        for label, value in attrs.items() if isinstance(attrs, dict) else []:
            key, text = name(label), text_value(value)
            if not key or not text or key in EXCLUDED or re.search(r'sku|asin|ean|upc|gtin|identifier|url|price|inventory|stock|brand|seller', key):
                continue
            if key in columns and columns[key]['value'] != text:
                columns[key] = dict(label=label, value=None)
            else:
                columns[key] = dict(label=label, value=text)
        by_sku[row['platform_sku']] = columns
    common = set.intersection(*(set(v) for v in by_sku.values())) if by_sku else set()
    columns = {}
    for key in sorted(common):
        values = {sku: cols[key]['value'] for sku, cols in by_sku.items()}
        if all(v is not None for v in values.values()):
            columns[key] = dict(label=next(iter(by_sku.values()))[key]['label'], values=values,
                                varying=len({value_key(v) for v in values.values()}) > 1)
    eligible = [k for k in allowed if name(k) not in FORBIDDEN]
    mappings, targets, pending = [], {}, []

    def assign(source, target, method, evidence=None):
        column = columns[source]
        values = {sku: adapt(v, props[target]) for sku, v in column['values'].items()}
        if any(v is None for v in values.values()):
            return False
        targets[target] = values
        mappings.append(dict(source=column['label'], target=target, method=method,
                             source_values=column['values'], values=values,
                             reason='来源同名属性' if method == 'exact' else '按 Skill 使用本地语义近似匹配；保留来源原值，非 AI 评分',
                             local_evidence=evidence or []))
        return True

    # Same-name fields win, including a constant Color used by the merge fallback.
    for key, column in columns.items():
        exact = [a for a in eligible if name(a) == key or name(props[a].get('title', '')) == key]
        if len(exact) == 1 and exact[0] not in targets and assign(key, exact[0], 'exact'):
            continue
        if column['varying']:
            pending.append(key)
    overflow = []
    for source in pending:
        candidates = [k for k in eligible if k not in targets and custom_field(k, props[k])
                      and all(adapt(v, props[k]) is not None for v in columns[source]['values'].values())]
        ranking = rank(columns[source]['label'], list(columns[source]['values'].values()), candidates, props)
        if ranking:
            assign(source, ranking[0]['target'], 'user_authorized_custom_fallback', ranking)
        else:
            overflow.append(source)
    for source in overflow:
        candidates = sorted((k for k in targets if custom_field(k, props[k])), key=lambda k: (name(k) != 'color', k))
        for target in candidates:
            combined = {}
            for sku, base in targets[target].items():
                if isinstance(base, list):
                    base = base[0] if len(base) == 1 else None
                combined[sku] = adapt(merge(base, columns[source]['values'][sku]), props[target]) if isinstance(base, str) else None
            if all(v is not None for v in combined.values()):
                targets[target] = combined
                mappings.append(dict(source=columns[source]['label'], target=target, method='combined_into_existing',
                                     reason='按 Skill 使用 ** 合并同一子 SKU 的来源属性，优先 Color',
                                     source_values=columns[source]['values'], values=combined, local_evidence=[]))
                break
    proposed = copy.deepcopy(rows)
    for row in proposed:
        for key, values in targets.items():
            row['visible'][key] = values[row['platform_sku']]
    return proposed, mappings
