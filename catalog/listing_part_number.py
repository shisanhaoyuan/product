"""The user's ERP Skill exception for MPN: purpose, then a short product name.

This is a user-selected listing value, never a claim of a manufacturer-issued
identifier. Keep it outside the general AI permission for protected fields.
"""
import re

from .listing_ai_client import ListingAIError
from .listing_skill_defaults import compact
from .walmart_listing_schema import plain, precise_validator

KEYS = {'partnumber', 'manufacturerpartnumber', 'mpn'}
USES = {'recommendedusesforproduct', 'recommendeduseforproduct'}
PLACEHOLDERS = {'', 'na', 'none', 'null', 'notapplicable', 'notavailable', 'unknown',
                'notprovided', 'unspecified', '无', '暂无', '不适用', '未提供'}


def part_number_field(key, shape):
    return compact(key) in KEYS or compact(shape.get('title', '')) in KEYS


def purpose_values(facts):
    for root in ('content.attributes', 'detail.attributes'):
        attrs = facts.get(root)
        if isinstance(attrs, dict):
            for key, value in attrs.items():
                if compact(key) in USES:
                    yield value, root + '.' + key
    verified = facts.get('verified_source') or {}
    for root, attrs in [('verified_source.raw_attributes', verified.get('raw_attributes')),
                        ('verified_source.copy.raw_attributes', (verified.get('copy') or {}).get('raw_attributes'))]:
        if isinstance(attrs, dict):
            for key, value in attrs.items():
                if compact(key) in USES:
                    yield value, root + '.' + key
    for index, attr in enumerate(facts.get('detail.ProductAttributeList') or []):
        if isinstance(attr, dict) and compact(attr.get('AttributeNameEn') or attr.get('AttributeName', '')) in USES:
            yield attr.get('AttributeValue'), f'detail.ProductAttributeList.{index}.AttributeValue'


def forbidden_values(row, facts):
    result = [row.get('source_sku'), row.get('platform_sku'), row.get('asin'),
              (row.get('identification') or {}).get('value'), row.get('visible', {}).get('brand'),
              facts.get('source_brand'), *(facts.get('source_brands') or [])]
    def identifiers(data):
        if isinstance(data, dict):
            for key, value in data.items():
                if compact(key.split('.')[-1]) in KEYS | {'model', 'modelname', 'modelnumber', 'serialnumber', 'asin', 'sku'}:
                    if isinstance(value, str):
                        result.append(value)
                identifiers(value)
        elif isinstance(data, list):
            for value in data:
                identifiers(value)
    identifiers(facts)
    return [v.strip() for v in result if isinstance(v, str) and v.strip()]


def safe_text(value, banned):
    if not isinstance(value, str):
        return ''
    value = plain(value).strip()
    if (compact(value) in PLACEHOLDERS or compact(value) in USES | KEYS
            or re.search(r'\b(?:B0[A-Z0-9]{8}|\d{8,14})\b', value, re.I)
            or any(re.search(r'(?<!\w)' + re.escape(v) + r'(?!\w)', value, re.I) for v in banned)):
        return ''
    return value


def limited(value, shape):
    limit = min(58, shape.get('maxLength', 58))
    size = lambda text: len(text.encode('utf-16-le')) // 2
    if size(value) > limit:
        words = value.split()
        while words and size(' '.join(words)) > limit:
            words.pop()
        value = ' '.join(words)
    return value if value and precise_validator(shape).is_valid(value) else None


def fill_part_number(row, shape, facts, ai):
    from .listing_generation import fact_reference_exists
    banned = forbidden_values(row, facts)
    for original, ref in purpose_values(facts):
        value = limited(safe_text(original, banned), shape)
        if value and fact_reference_exists(facts, ref):
            return value, dict(kind='user_rule', value=value, refs=[ref], source_value=original,
                reason='user_authorized_rule：按原 Skill 使用同 SKU Recommended Uses For Product 用途原文；最多58字符，不是厂家真实零件编号')
    proposal = ai.query('skill_part_number', dict(source_sku=row['source_sku'], facts=facts,
        field_schema=shape, max_characters=min(58, shape.get('maxLength', 58)),
        instructions='User-authorized ERP Skill exception for Part Number / Manufacturer Part Number / MPN: '
            'summarize what THIS product is as a short English product name using its title, description and category. '
            'Use only current source_sku facts, never the example name from a rule. '
            'Do not use brand names, model/part/serial numbers, SKU, ASIN or barcodes. '
            'This is a user_rule listing label, NOT a manufacturer-issued part number. '
            'Do not add unsupported functions. Keep complete words within max_characters.',
        output_contract={'source_sku': row['source_sku'], 'value': 'short English product name',
                         'refs': ['actual current-SKU fact paths'], 'reason': '中文概括依据'}))
    value = limited(safe_text(proposal.get('value'), banned), shape)
    refs = proposal.get('refs')
    if (proposal.get('source_sku') != row['source_sku'] or not value
            or not re.search('[A-Za-z]', value) or re.search('[\u3400-\u9fff]', value)
            or not isinstance(refs, list) or not refs or any(not fact_reference_exists(facts, ref) for ref in refs)
            or not isinstance(proposal.get('reason'), str) or not proposal['reason'].strip()):
        raise ListingAIError('零件编号的 Skill 用途／商品名称填充未通过同 SKU、来源或长度校验')
    evidence = dict(kind='user_rule', value=value, refs=refs,
        reason='user_authorized_rule：用途缺失，按原 Skill 概括本商品简短英文名称；不是厂家真实零件编号。' + proposal['reason'][:400])
    return value, evidence
