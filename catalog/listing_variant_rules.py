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
COUNT_NAMES = {'numberofitems','numberofpacks','itempackagequantity','packagequantity',
               'multipackquantity','countperpack','packcount','packquantity','数量','包装数量','件数'}
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
    return {'colour': 'color', 'basecolor': 'color', 'colorname': 'color', 'colourname': 'color',
            'maincolor': 'color', 'mainmaterial': 'material', '颜色': 'color',
            'sizename': 'size', '尺寸': 'size', '图案': 'pattern', '形状': 'shape',
            'finishtype': 'finish', '饰面': 'finish'}.get(key, key)


def text_value(value):
    from .listing_source_attributes import unwrap
    value = unwrap(value)
    if isinstance(value, list) and len(value) == 1:
        value = unwrap(value[0])
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value).strip()
        return text if text and text.casefold() not in {'n/a', 'unknown', 'none', 'null', '未采集', '未知', '待核验'} else None
    return None


def pack_count(attrs):
    """Use explicit pack counts, never stock, shelf counts, model numbers or titles."""
    found = set()
    for label, value in attrs.items():
        if name(label) not in COUNT_NAMES and name(label) != 'size':
            continue
        text = text_value(value)
        if not text:
            continue
        if name(label) in COUNT_NAMES:
            match = re.fullmatch(r'(?:pack\s+of\s*|set\s+of\s*)?(\d+)(?:\s*[- ]?\s*(?:packs?|pieces?|pcs|件套|件))?', text, re.I)
            matches = [match] if match else []
        else:
            # A size such as "5-Tier (2 Pack)" explicitly identifies a pack of
            # two; the shelf count or a dimension alone must never supply it.
            matches = re.finditer(r'(?<![\w.])(?:pack\s+of\s*|set\s+of\s*)(\d+)(?![\d.])|(?<![\w.])(\d+)\s*[- ]?\s*(?:packs?|pieces?|pcs|件套|件)(?!\w)', text, re.I)
        for match in matches:
            count = int(next(v for v in match.groups() if v is not None))
            if count > 0:
                found.add(count)
    return next(iter(found)) if len(found) == 1 else None


def quantity_colors(rows, content, shape):
    """Disambiguate equal observed colors: Brown / Brown**2, with explicit counts."""
    from .listing_variants import source_value, value_key
    if not custom_field('color', shape):
        return {}, {}
    colors = {r['platform_sku']: source_value(content.get(r['platform_sku'], {}), 'color', shape) for r in rows}
    counts = {r['platform_sku']: pack_count(content.get(r['platform_sku'], {}).get('attributes') or {}) for r in rows}
    result = copy.deepcopy(colors)
    used = {}
    for value in {value_key(c) for c in colors.values() if isinstance(c, str)}:
        skus = [sku for sku, color in colors.items() if isinstance(color, str) and value_key(color) == value]
        if len(skus) < 2 or any(counts[s] is None for s in skus) or len({counts[s] for s in skus}) < 2:
            continue
        for sku in skus:
            result[sku] = colors[sku] if counts[sku] == 1 else merge(colors[sku], str(counts[sku]))
            if adapt(result[sku], shape) is None:
                return {}, {}
            used[sku] = counts[sku]
    return (result, used) if used and all(isinstance(v, str) for v in result.values()) else ({}, {})


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
    # The user explicitly wants pack differences in Color even if another custom
    # field could hold them. Keep the single pack's observed color unchanged.
    colors, counts = quantity_colors(rows, content, props.get('color', {})) if 'color' in eligible else ({}, {})
    if colors:
        targets['color'] = colors
        mappings.append(dict(source='Color + pack quantity', target='color', method='user_rule_color_pack_quantity',
            source_values={sku: content.get(sku, {}).get('attributes') or {} for sku in colors}, values=colors,
            reason='用户规则：同色不同件数，单件保留原颜色，多件以 **件数 区分；数量来自同一子商品', local_evidence=[]))
        if len({value_key(v) for v in colors.values()}) == len(rows):
            pending = []  # Already distinct: don't append unrelated dimensions.
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
