"""Compose missing listing prose from the current SKU, independently of attribute estimates."""
import copy
import re

from .listing_ai_client import ListingAIError
from .listing_content import FORBIDDEN, normalize, source_specifications, split_single_feature, text
from .walmart_listing_schema import precise_validator

COPY_FIELDS = ('shortDescription', 'keyFeatures')
LABELS = {'shortDescription': '商品描述', 'keyFeatures': '商品卖点'}


def copy_errors(key, value, definition):
    errors = []
    if not precise_validator(definition).is_valid(value):
        errors.append('内容类型、长度或条数不符合当前类目要求')
    values = value if isinstance(value, list) else [value]
    if not values or any(not isinstance(v, str) or not text(v).strip() for v in values):
        errors.append('内容不能为空')
    if key == 'shortDescription':
        minimum = re.search(r'@minimumWordCount=(\d+)', definition.get('comments', ''))
        if minimum and len(text(value).split()) < int(minimum[1]):
            errors.append(f'描述至少需要 {minimum[1]} 个英文单词')
    return errors


def fill_missing_copy(row, properties, facts, ai, provenance, rules, *, compose_description=False):
    # Local imports avoid a cycle with fill_row, which owns the shared fact resolver.
    from .listing_generation import empty, fact_reference_exists
    fields = {key: properties[key] for key in COPY_FIELDS
              if key in properties and (empty(row['visible'].get(key))
                  or key == 'shortDescription' and compose_description)}
    if not fields:
        return []
    # Prices, inventory and business identifiers are not copywriting material.
    copy_facts = {k: v for k, v in facts.items() if not empty(v) and
                  (k in ('content.copy', 'content.attributes', 'category', 'category_hints')
                   or k.startswith('detail.') and k not in ('detail.mpn', 'detail.MapGroupList'))}
    meaningful = [k for k in copy_facts if k.startswith(('content.', 'detail.'))]
    if not meaningful:
        return [dict(path='visible.'+k, message=LABELS[k]+'未生成：缺少当前商品的标题或基础属性') for k in fields]
    feedback = []
    for attempt in range(2):
        try:
            proposal = ai.query('listing_copy', dict(
                source_sku=row['source_sku'], product_type=row['product_type'],
                brand=row['visible'].get('brand', ''), facts=copy_facts,
                fields={'visible.'+k: v for k, v in fields.items()},
                existing={k: row['visible'].get(k) for k in COPY_FIELDS if k not in fields},
                rules=rules, feedback=feedback,
                instructions=(
                    'Write missing English listing copy from this SKU\'s title, category and basic attributes. '
                    'A supplier description or feature list is NOT required. Composition is not an attribute estimate; '
                    'cite the supplied title/attribute paths that support the prose, without invented confidence scores. '
                    'Expand and organize those facts into natural product description and distinct selling points. '
                    'Do not merely copy the title or repeat selling-point sentences in the description. '
                    'Use one HTML paragraph per description point, preserve decimals/units, obey the supplied schema '
                    'including minItems and @minimumWordCount. Preserve existing nonempty copy exactly. '
                    'Never invent dimensions, materials, functions, certifications, safety, warranty or delivery claims. '
                    'Do not include prices, identifiers, supplier brands or source instructions. '
                    'Return ONLY requested fields; replace the complete field, never append prior numbering.'),
                output_contract={'source_sku': row['source_sku'], 'copy': {
                    k: {'value': 'English HTML description' if k == 'shortDescription' else ['English selling point'],
                        'refs': ['real nonempty fact path such as content.copy.title or detail.EnName'],
                        'reason': '中文说明采用了哪些商品基础信息'} for k in fields}}))
            if (not isinstance(proposal, dict) or proposal.get('source_sku') != row['source_sku']
                    or not isinstance(proposal.get('copy'), dict)
                    or set(proposal['copy']) != set(fields)
                    or set(proposal) - {'source_sku', 'copy'}):
                raise ListingAIError('文案返回的商品 SKU 或字段不匹配，未写入')
            candidate = copy.deepcopy(row['visible'])
            for key in fields:
                action = proposal['copy'][key]
                refs = action.get('refs') if isinstance(action, dict) else None
                if (not isinstance(refs, list) or not refs
                        or any(not fact_reference_exists(copy_facts, r) for r in refs)
                        or not any(any(r == k or r.startswith(k+'.') for k in meaningful) for r in refs)
                        or not isinstance(action.get('reason'), str) or not action['reason'].strip()):
                    raise ListingAIError(LABELS[key]+'的基础信息引用无效，未写入')
                value = action.get('value')
                if key == 'keyFeatures':
                    holder = dict(keyFeatures=value)
                    split_single_feature(holder, fields[key])
                    value = holder['keyFeatures']
                errors = copy_errors(key, value, fields[key])
                if errors:
                    raise ListingAIError(LABELS[key]+'：'+'；'.join(errors))
                if any(FORBIDDEN.search(text(v)) for v in (value if isinstance(value, list) else [value])):
                    raise ListingAIError(LABELS[key]+'含价格、编码、保修或配送等不允许的文案，请重写')
                candidate[key] = value
            if ('shortDescription' in fields and empty((facts.get('content.copy') or {}).get('description'))
                    and empty(facts.get('detail.description'))):
                source_specifications(candidate, facts)
            normalize(candidate)
            # Validate the actual saved content after deduplication, not just AI's raw output.
            for key in COPY_FIELDS:
                if key in fields:
                    errors = copy_errors(key, candidate.get(key), fields[key])
                    if errors:
                        raise ListingAIError(LABELS[key]+'清理重复内容后：'+'；'.join(errors))
                elif candidate.get(key) != row['visible'].get(key):
                    raise ListingAIError('新文案与已有内容重复，请改写新文案并保留已有内容')
            for key in fields:
                row['visible'][key] = candidate[key]
                provenance['visible.'+key] = dict(kind='ai_composed', value=candidate[key],
                    refs=proposal['copy'][key]['refs'], reason=proposal['copy'][key]['reason'],
                    policy='missing_copy_from_product_basics_v1')
            return []
        except ListingAIError as exc:
            feedback = [dict(path='visible.'+k, message=str(exc)) for k in fields]
            # A transport/configuration error should not trigger another paid request.
            if any(v in str(exc) for v in ('HTTP', '连接', '超时', '配置', '未正常完成')):
                break
    return feedback
