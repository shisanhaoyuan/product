"""SKU-scoped deterministic mappings and bounded AI proposals over official schemas."""
import copy
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from jsonschema import Draft7Validator

from .listing_ai_client import ListingAIError
from .walmart_listing_schema import plain, sections, feed_item, schema_errors, identifier_errors, precise_validator
from .listing_skill_defaults import has_default, skill_default, length_fallback

RULES_VERSION = 'listing-generation-v3'
RULES = Path(__file__).with_name('listing_generation_rules.md').read_text()
BLOCKED = re.compile(r'prop.?65|warranty|certif|nrtl|chemical|origin|warning|exempt|gtin|upc|ean|model|partnumber', re.I)
PROTECTED = {'sku','productIdentifiers','inventory','price','msrp','SkuUpdate','ProductIdUpdate','fulfillmentCenterID'}
CONTENT_FIELDS = {'productName','brand','shortDescription','keyFeatures','mainImageUrl','productSecondaryImageURL'}


def empty(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not plain(value).strip()
    if isinstance(value, (list, dict)):
        return not value or all(empty(v) for v in (value.values() if isinstance(value, dict) else value))
    return False


def norm(value):
    return re.sub(r'\s+', ' ', str(value).casefold().replace('&', ' and ')).strip()


def fact_reference_exists(facts, ref):
    """Resolve actual dotted fact paths, including dotted root keys and list indexes.

    Facts deliberately have roots such as content.copy and detail.attributes.
    A model may cite the precise leaf content.copy.title or an attribute whose
    name contains spaces/dots; accept only a present, nonempty value from facts.
    """
    if not isinstance(ref,str) or not ref or len(ref)>500:
        return False

    def resolve(value, path):
        if not path:
            return not empty(value)
        if isinstance(value,dict):
            # Prefer the longest actual key. Do not invent a property by
            # splitting an existing dotted name into unrelated short names.
            for key in sorted((k for k in value if isinstance(k,str)),key=len,reverse=True):
                if path==key:
                    return not empty(value[key])
                if path.startswith(key+'.'):
                    return resolve(value[key],path[len(key)+1:])
        elif isinstance(value,list):
            index,_,rest=path.partition('.')
            if index.isascii() and index.isdigit() and len(index)<10 and int(index)<len(value):
                return resolve(value[int(index)],rest)
        return False

    return resolve(facts,ref)


def required_keys(schema, value):
    result = set(schema.get('required', []))
    for branch in schema.get('allOf', []):
        result.update(required_keys(branch, value))
    if 'if' in schema:
        branch = schema.get('then' if Draft7Validator(schema['if']).is_valid(value) else 'else', {})
        result.update(required_keys(branch, value))
    for keyword in ('oneOf', 'anyOf'):
        valid = [s for s in schema.get(keyword, []) if Draft7Validator(s).is_valid(value)]
        if len(valid) == 1:
            result.update(required_keys(valid[0], value))
    for key, dep in schema.get('dependencies', {}).items():
        if key in value:
            result.update(dep if isinstance(dep, list) else required_keys(dep, value))
    return result


def source_facts(source, item, variant):
    """Never attach a parent's/another variant's facts to a child."""
    content = variant.get('content') or {}
    facts = {f'content.{k}': v for k, v in content.items()
             if k in ('copy','attributes','shipping_weight') and not empty(v)}
    facts['category'] = item.get('category', '')
    facts['category_hints'] = item.get('category_hints', [])
    if source.get('brand'):
        facts['source_brand'] = source['brand']
    if source.get('source') != 'GigaB2B':
        if source.get('variants'):
            own = [v for v in source['variants'] if v.get('sku') == variant['sku']]
            if len(own) != 1:
                raise ListingAIError('来源子 SKU 不唯一，已停止生成')
            if own[0].get('source_facts'):
                facts['verified_source'] = own[0]['source_facts']
        elif source.get('sku') == variant['sku'] and source.get('source_facts'):
            facts['verified_source'] = source['source_facts']
    if source.get('source') == 'GigaB2B':
        detail = ((source.get('source_facts') or {}).get('giga') or {}).get('detail') or {}
        if source.get('sku') != variant['sku'] or detail.get('sku') != variant['sku']:
            raise ListingAIError('云仓详情与当前来源 SKU 不一致，已停止生成')
        allowed = ['productName','description','characteristics','attributes','mainColor','mainMaterial',
                   'placeOfOrigin','mpn','assembledLength','assembledWidth','assembledHeight','assembledWeight',
                   'assembledLengthUnit','assembledWeightUnit','length','width','height','weight','lengthUnit',
                   'weightUnit','lengthCm','widthCm','heightCm','weightKg','comboInfo','comboFlag','customList']
        facts.update({f'detail.{k}': detail[k] for k in allowed if k in detail and not empty(detail[k])})
    return facts


def prepare_copy(row, facts, provenance):
    """Only normalize untouched source copy; preserve manual/previously generated text."""
    original = facts.get('content.copy') or {}
    brand = row['visible'].get('brand', '')
    old_brand = facts.get('source_brand', '')
    forbidden = re.compile(r'\b(?:manufacturer|warranty|UPC|EAN|GTIN|ASIN|best sellers rank|customer reviews)\b', re.I)
    def clean(text, description=False):
        text = plain(text)
        if old_brand and brand and norm(old_brand) != norm(brand):
            text = re.sub(r'(?<!\w)' + re.escape(old_brand) + r'(?!\w)', lambda _:brand,text,flags=re.I)
        if description:
            parts = re.split(r'\n+|(?<=[.!?])\s+',text)
            text = '\n'.join(dict.fromkeys(p.strip() for p in parts if p.strip() and not forbidden.search(p)))
        return text
    for field, source_key in [('productName','title'),('shortDescription','description'),('keyFeatures','features')]:
        value = row['visible'].get(field)
        if value != original.get(source_key) or empty(value):
            continue
        cleaned = [clean(v,True) for v in value] if isinstance(value,list) else clean(value,field!='productName')
        if isinstance(cleaned,list):
            cleaned = list(dict.fromkeys(v for v in cleaned if v))
        if cleaned != value:
            row['visible'][field] = cleaned
            provenance['visible.'+field] = dict(kind='user_rule',value=cleaned,refs=['content.copy'],reason='品牌同步与来源文案清理，保留事实')


def source_copy(key, schema, facts, brand):
    """Recover missing copy using this SKU only, including a feature-based description."""
    original = facts.get('content.copy') or {}
    old_brand = facts.get('source_brand', '')
    def clean(value):
        value = plain(value)
        if old_brand and brand:
            value = re.sub(r'(?<!\w)'+re.escape(old_brand)+r'(?!\w)',lambda _:brand,value,flags=re.I)
        sentences = re.split(r'\n+|(?<=[.!?])\s+',value)
        return '\n'.join(dict.fromkeys(v.strip() for v in sentences if v.strip() and not re.search(
            r'\b(?:Amazon|ASIN|SKU|UPC|EAN|GTIN|manufacturer|warranty|shipping|delivery|discount|price|reviews)\b',v,re.I)))
    refs = ['content.copy']
    if key == 'productName':
        value = clean(original.get('title', ''))
    elif key == 'keyFeatures':
        value = list(dict.fromkeys(clean(v) for v in original.get('features', []) if isinstance(v,str)))
        value = [v for v in value if v]
    elif key == 'shortDescription':
        value = clean(original.get('description', ''))
        if not value:
            features = list(dict.fromkeys(clean(v) for v in original.get('features', []) if isinstance(v,str)))
            features = [v for v in features if v]
            parts = ['Features:\n'+'\n'.join(f'{i}. {v}' for i,v in enumerate(features,1))] if features else []
            attrs = facts.get('content.attributes') or facts.get('detail.attributes') or {}
            if isinstance(attrs,dict):
                specifications = list(dict.fromkeys(clean(f'{k}: {v}') for k,v in attrs.items()
                    if isinstance(v,(str,int,float)) and not empty(v)))
                specifications = [v for v in specifications if v]
                if specifications:
                    parts.append('Product Information:\n'+'\n'.join(specifications))
                    refs.append('content.attributes' if 'content.attributes' in facts else 'detail.attributes')
            value = '\n\n'.join(parts)
    else:
        return None, []
    # Never duplicate text to meet minimum lengths, or truncate a word/unit.
    if isinstance(value,str) and len(value) > schema.get('maxLength',len(value)):
        value = value[:schema['maxLength']].rsplit(' ',1)[0].rstrip()
    refs = [ref for ref in refs if fact_reference_exists(facts,ref)]
    return (candidate_value(schema,value), refs) if not empty(value) and refs else (None, [])


def choose_type(row, item, facts, types, ai):
    hints = {norm(v) for value in [item.get('category', ''), *item.get('category_hints', [])]
             for v in [value, re.split(r'>+|/|→|\||::', value)[-1]] if v}
    matches = [t for t in types if norm(t['name']) in hints]
    # An already selected type is rechecked too: older mappings may select Outdoor
    # Sofas for a Primary Living Space sofa. Never silently keep that mismatch.
    living = any('primary living space' in h or 'indoor' in h for h in hints)
    outdoor = any('outdoor' in h or 'patio' in h for h in hints)
    conflict = living and 'outdoor' in norm(row['product_type']) or outdoor and row['product_type'] and 'outdoor' not in norm(row['product_type'])
    if row['product_type'] and not conflict:
        if row['product_type'] in {t['name'] for t in types}:
            return row['product_type'], dict(kind='existing', reason='保留现有有效商品类型', refs=[])
    if len(matches) == 1 and not (living and 'outdoor' in norm(matches[0]['name'])):
        return matches[0]['name'], dict(kind='source', reason='来源类目唯一准确匹配', refs=['category'])
    # Token retrieval reduces the prompt, but cannot manufacture an option.
    words = set(re.findall(r'[a-z]{3,}', ' '.join(hints) + ' ' + str(facts.get('detail.productName', ''))))
    ranked = sorted(types, key=lambda t: -len(words & set(re.findall(r'[a-z]{3,}', norm(t['name'] + ' ' + t['group'])))))
    candidates = ranked[:60]
    proposal = ai.query('product_type', dict(source_sku=row['source_sku'], facts=facts,
        candidates=candidates, existing=row['product_type'], rules=RULES,
        output_contract={'source_sku': row['source_sku'], 'product_type': 'exact candidate name or empty',
                         'reason': '中文选择理由或冲突原因',
                         'refs': ['existing fact key or nested dotted path, e.g. content.copy.title'],
                         'confidence': 'number 0..1'}))
    pt = proposal.get('product_type', '')
    refs = proposal.get('refs', [])
    confidence = proposal.get('confidence')
    if proposal.get('source_sku') != row['source_sku']:
        raise ListingAIError('AI 返回的来源 SKU 与当前商品不一致，未采用类目')
    if not isinstance(pt,str) or not pt or pt not in {t['name'] for t in candidates}:
        raise ListingAIError('AI 未选择当前店铺的有效商品类型，请重新生成')
    if type(confidence) not in (int,float) or not 0.75 < confidence <= 1:
        raise ListingAIError('AI 类目判断的置信度不足或格式无效，未强行选择')
    if not isinstance(refs,list) or not refs or any(not fact_reference_exists(facts,r) for r in refs):
        raise ListingAIError('AI 类目依据引用不存在或内容为空，未采用结果')
    if not isinstance(proposal.get('reason'),str) or not proposal['reason'].strip():
        raise ListingAIError('AI 未提供类目选择理由，未采用结果')
    if living and 'outdoor' in norm(pt):
        raise ListingAIError('来源明确为室内用途，AI 却选择户外商品类型，未采用结果')
    return pt, dict(kind='ai_source', reason=proposal['reason'], refs=refs, confidence=confidence)


def converted(value, unit, target, weight=False):
    factors = {'lb': Decimal('1'), 'lbs': Decimal('1'), 'kg': Decimal('2.2046226218'), 'g': Decimal('0.0022046226218')} if weight else {
        'in': Decimal('1'), 'inch': Decimal('1'), 'inches': Decimal('1'), 'cm': Decimal('0.3937007874'), 'mm': Decimal('0.03937007874'), 'ft': Decimal('12')}
    try:
        n = Decimal(str(value)) * factors[str(unit).strip().lower()] / factors[target]
        return float(n.quantize(Decimal('.0001'))) if n.is_finite() and n > 0 else None
    except (InvalidOperation, KeyError, ValueError):
        return None


def candidate_value(schema, value):
    if value is None:
        return None
    if schema.get('type') == 'array' and isinstance(value, str):
        value = [value]
    if schema.get('enum') and isinstance(value, str):
        matches = [v for v in schema['enum'] if norm(v) == norm(value)]
        value = matches[0] if len(matches) == 1 else value
    return value if precise_validator(schema).is_valid(value) else None


def mapped_value(key, schema, facts):
    aliases = {
        'color': ['detail.mainColor'], 'material': ['detail.mainMaterial'],
        'manufacturerPartNumber': ['detail.mpn'],
        'country_of_origin_substantial_transformation': ['detail.placeOfOrigin'],
        'countryOfOriginSubstantialTransformation': ['detail.placeOfOrigin'],
    }
    for ref in aliases.get(key, []):
        v = candidate_value(schema, facts.get(ref))
        if v is not None:
            return v, [ref]
    attrs = facts.get('content.attributes') or facts.get('detail.attributes') or {}
    compact = lambda v: re.sub(r'[^a-z0-9]', '', norm(v))
    for name, value in attrs.items() if isinstance(attrs, dict) else []:
        if compact(name) in {compact(key),compact(schema.get('title',''))} or (key in ('color','material') and norm(name) == 'main ' + key):
            v = candidate_value(schema, value)
            if v is not None:
                return v, ['content.attributes' if 'content.attributes' in facts else 'detail.attributes']
    dimensions = {'assembledProductLength':'assembledLength', 'assembledProductWidth':'assembledWidth',
                  'assembledProductHeight':'assembledHeight', 'assembledProductDepth':'assembledWidth',
                  'assembledProductWeight':'assembledWeight'}
    if key in dimensions:
        field = dimensions[key]; weight = field.endswith('Weight')
        unit_key = 'detail.assembledWeightUnit' if weight else 'detail.assembledLengthUnit'
        units = schema.get('properties', {}).get('unit', {}).get('enum', [])
        for unit in units:
            number = converted(facts.get('detail.'+field), facts.get(unit_key), unit, weight)
            v = candidate_value(schema, {'measure':number, 'unit':unit}) if number is not None else None
            if v is not None:
                return v, ['detail.'+field, unit_key]
    if key == 'ShippingWeight':
        # Multi-package shipping weight cannot be inferred from product weight.
        if not facts.get('detail.comboFlag'):
            number = converted(facts.get('detail.weight'), facts.get('detail.weightUnit'), 'lb', True)
            if number is not None and candidate_value(schema, number) is not None:
                return number, ['detail.weight','detail.weightUnit']
    return None, []


def fill_row(row, schema, facts, ai, provenance):
    row = copy.deepcopy(row)
    spec = sections(schema, row['product_type'])
    # A class change may leave old fields in the document. Keep their full values
    # in the audit so cleanup is recoverable, without sending unsupported fields.
    for section in ('visible','orderable'):
        definition = spec[section]
        for key,value in list(row[section].items()):
            unsupported = (definition.get('additionalProperties') is False and
                key not in definition.get('properties', {}) and
                not any(re.search(pattern,key) for pattern in definition.get('patternProperties', {})))
            if key not in PROTECTED and (unsupported and key not in CONTENT_FIELDS or empty(value)):
                del row[section][key]
                if not empty(value):
                    provenance[section+'.'+key] = dict(kind='schema_cleanup',value=None,previous_value=value,
                        refs=[],reason='当前商品类型不接受此旧字段，原值保留在审计中')
    prepare_copy(row,facts,provenance)
    problems = []
    for turn in range(3):
        missing = {}
        for section in ('visible','orderable'):
            props = spec[section].get('properties', {})
            needed = required_keys(spec[section], row[section])
            needed |= {k for k,s in props.items() if has_default(k,s)}
            if section == 'visible':
                needed |= {'productName','brand','shortDescription','keyFeatures','mainImageUrl'}
            for key in sorted(needed):
                if key in PROTECTED or key not in props:
                    continue
                field = section + '.' + key
                current = row[section].get(key)
                if not empty(current) and key != 'netContent':
                    continue
                v, refs = mapped_value(key, props[key], facts) if empty(current) else (None, [])
                kind = 'source'
                reason = '来源映射／确定单位换算'
                if v is None and section == 'visible':
                    v,refs = source_copy(key,props[key],facts,row['visible'].get('brand',''))
                if v is None:
                    v, reason = skill_default(key,props[key],current,facts)
                    refs, kind = [], 'user_rule'
                if v is not None:
                    if v != current:
                        row[section][key] = v
                        provenance[field] = dict(kind=kind, refs=refs, value=v, reason=reason)
                else:
                    if not empty(current):
                        continue
                    if BLOCKED.search(key):
                        problems.append(dict(path=field,message='缺少该字段的明确来源或证明，不能估算'))
                    else:
                        missing[field] = props[key]
        if not missing:
            break
        try:
            proposal = ai.query('required_attributes', dict(source_sku=row['source_sku'],
                product_type=row['product_type'], brand=row['visible'].get('brand'), facts=facts,
                fields=missing, existing=row, rules=RULES,
                output_contract={'source_sku':row['source_sku'], 'actions':[
                    {'field':'visible.field or orderable.field', 'value':'schema value','kind':'source or estimate',
                     'refs':['existing fact key or nested dotted path, e.g. content.copy.features.0'],
                     'reason':'中文依据', 'confidence':'number 0..1'}],
                     'missing':[{'field':'path','reason':'中文缺失原因'}]}))
            if proposal.get('source_sku') != row['source_sku'] or not isinstance(proposal.get('actions'), list):
                raise ListingAIError('AI 返回的商品 SKU 或属性计划不匹配')
            seen = set()
            accepted = []
            for action in proposal['actions']:
                field = action.get('field') if isinstance(action, dict) else None
                if field not in missing or field in seen:
                    raise ListingAIError('AI 返回未知或重复属性，未采用该轮结果')
                seen.add(field)
                section, key = field.split('.', 1)
                value = candidate_value(missing[field], action.get('value'))
                refs = action.get('refs', [])
                confidence = action.get('confidence')
                kind = action.get('kind')
                if (value is None or type(confidence) not in (int,float) or not 0 < confidence <= 1
                        or not isinstance(action.get('reason'), str) or not action['reason'].strip()
                        or kind not in ('source','estimate') or not isinstance(refs, list)
                        or any(not fact_reference_exists(facts,r) for r in refs)
                        or kind == 'source' and (confidence <= .75 or not refs)
                        or kind == 'estimate' and BLOCKED.search(key)):
                    problems.append(dict(path=field, message='AI 属性值、证据或估算权限未通过校验'))
                    continue
                accepted.append((section,key,value,action))
            for section,key,value,action in accepted:
                row[section][key] = value
                provenance[section+'.'+key] = action
            for issue in proposal.get('missing', []):
                if isinstance(issue, dict) and issue.get('field') in missing and isinstance(issue.get('reason'), str):
                    problems.append(dict(path=issue['field'], message=issue['reason'][:400]))
        except ListingAIError as exc:
            problems.extend(dict(path=path, message=str(exc)) for path in missing)
            break
    # Source and model estimates get priority. The Skill's 22-inch rule is a
    # deterministic final fallback; never applied to weights or identifiers.
    for section in ('visible','orderable'):
        props = spec[section].get('properties', {})
        for key in required_keys(spec[section],row[section]):
            if key in PROTECTED or key not in props:
                continue
            current = row[section].get(key)
            if current is not None and candidate_value(props[key],current) is not None and not empty(current):
                continue
            value = length_fallback(key,props[key],current)
            if value is not None:
                row[section][key] = value
                provenance[section+'.'+key] = dict(kind='user_rule',value=value,refs=[],
                    reason='user_authorized_rule: unavailable parameter default 22 inch；来源缺失且 AI 未能补齐的长度兜底，非实测值')
    # A later conditional pass may have filled a previously missing field.
    problems = [p for p in problems if not p['path'] or
                empty(row.get(p['path'].split('.')[0], {}).get(p['path'].split('.')[-1]))]
    return row, problems


def validate_generated(row, schema, version, origin):
    errors = [dict(path='identification', message=m) for m in identifier_errors(row['identification'])]
    if row['identification']['mode'] == 'exemption':
        errors.append(dict(path='identification',message='豁免仍需在正式校验时核对该店铺的批准记录'))
    if not row['fulfillment_center_id']:
        errors.append(dict(path='fulfillment_center_id', message='没有唯一可用的店铺发货仓，请设置店铺默认仓'))
    try:
        entry, validation_schema = feed_item(row, schema, version, origin, stock=10)
        errors.extend(schema_errors(validation_schema, entry, version))
    except (ValueError, KeyError, TypeError) as exc:
        errors.append(dict(path='', message='资料结构不完整，请查看缺失字段'))
    return errors
