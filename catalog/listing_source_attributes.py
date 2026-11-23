"""Read a child's retained source dimensions without inheriting sibling facts."""
import copy


def unwrap(value):
    while isinstance(value, dict) and len(value) == 1 and next(iter(value)) in {'variant_value', 'val', 'value'}:
        value = next(iter(value.values()))
    return value


def attributes(child):
    from .listing_variant_rules import name, COUNT_NAMES
    result, seen = {}, set()
    facts = child.get('source_facts') or {}
    sources = [child.get('attributes') or {}, facts.get('raw_attributes') or {},
               (facts.get('copy') or {}).get('raw_attributes') or {}]
    dimensions = {'color','size','pattern','finish','theme','shape','material','style',
                  'framecolor','bedsize','sizedescriptor','snaredrumsize','drumshellcolor'} | COUNT_NAMES
    for index, source in enumerate(sources):
        layer_keys = set()
        for label, value in source.items() if isinstance(source, dict) else []:
            key = name(label)
            value = unwrap(value)
            if key in seen or index and key not in dimensions:
                continue
            if value in (None, '', [], {}):
                continue
            result[label] = copy.deepcopy(value)
            layer_keys.add(key)
        seen.update(layer_keys)
    return result


def contents(basis, sources):
    """Frozen content wins; fill gaps only from the uniquely matched current child."""
    result = {}
    for item in basis['items']:
        source = sources.get(item['listing_id']) or {}
        for variant in item['variants']:
            content = copy.deepcopy(variant.get('content') or {})
            candidates = [v for v in source.get('variants') or [source]
                          if isinstance(v, dict) and v.get('sku') == variant.get('sku')]
            own = candidates[0] if len(candidates) == 1 else {}
            if variant.get('asin') and own.get('asin') != variant['asin']:
                own = {}
            frozen = attributes({'attributes': content.get('attributes') or {}})
            # Reuse the same canonical-key priority rules for current raw evidence.
            content['attributes'] = attributes({'attributes': frozen, 'source_facts': {'raw_attributes': attributes(own)}})
            result[variant['platform_sku']] = content
    return result
