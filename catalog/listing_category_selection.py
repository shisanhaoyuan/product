"""Retrieve official product types; source wording is a search hint, not a mapping."""
import json
import math
import re
from collections import Counter

from .listing_ai_client import ListingAIError


def tokens(value):
    def singular(word):
        if word.endswith('ies'):
            return word[:-3] + 'y'
        return word[:-1] if word.endswith('s') and not word.endswith('ss') else word
    return {singular(w) for w in re.findall(r'[a-z]{3,}', str(value).casefold())}


def ranked_types(types, item, facts, row):
    title = str((facts.get('content.copy') or {}).get('title') or facts.get('detail.productName')
                or facts.get('detail.EnName') or row.get('visible', {}).get('productName') or '')
    paths = [item.get('category', ''), *item.get('category_hints', [])]
    leaves = ' '.join(re.split(r'>+|/|→|\||::', value)[-1] for value in paths if value)
    title_words = tokens(title)
    leaf_words = tokens(leaves)
    context = tokens(json.dumps(facts, ensure_ascii=False))
    # Expand retrieval only. AI must still select a current-store official type
    # using this SKU's evidence; these aliases never assign a product type.
    expanded = set()
    if (title_words | leaf_words) & {'canopy', 'awning', 'sunshade', 'eave'}:
        expanded.update({'canopy', 'awning', 'sunshade'})
    if 'rain' in title_words and title_words & {'door', 'window', 'eave'}:
        expanded.add('awning')
    frequency = Counter(word for option in types for word in tokens(option['name']))
    weight = lambda words: sum(1 + math.log((len(types) + 1) / (frequency[word] + 1)) for word in words)

    def score(option):
        name = tokens(option['name'])
        group = tokens(option.get('group', ''))
        category = tokens(option.get('category', ''))
        description = tokens(str(option.get('description', ''))[:1000])
        return (6 * weight(name & title_words) + 8 * weight(name & leaf_words)
                + 5 * weight(name & expanded) + weight(name & context)
                + 2 * len(group & (title_words | leaf_words | expanded))
                + len((category | description) & (title_words | leaf_words)))
    return sorted(types, key=lambda option: -score(option))


def recovery_candidates(types, facts, source_sku, ai):
    """Offer the complete compact taxonomy; route through groups if it is large.

    One recovery selection only, without unbounded paging or retries. Never
    truncate the full taxonomy silently back to the same first sixty options.
    """
    compact = [{k: option.get(k, '') for k in ('name', 'group', 'category')} for option in types]
    fits = lambda value: len(json.dumps(value, ensure_ascii=False).encode()) <= 120000
    if fits(compact):
        return compact
    groups = {}
    for option in compact:
        groups.setdefault((option['category'], option['group']), []).append(option)
    keys = list(groups)
    choices = [dict(id=str(i), category=key[0], group=key[1]) for i, key in enumerate(keys)]
    if not fits(choices):
        raise ListingAIError('店铺类目目录过大，自动检索未完成，请搜索并手动选择商品类型')
    proposal = ai.query('product_type_groups', dict(source_sku=source_sku, facts=facts,
        candidates=choices,
        instructions='Select up to three official groups by the actual product and use, including synonyms. '
                     'Source categories need not have the same names as Walmart groups.',
        output_contract={'source_sku': source_sku, 'group_ids': ['exact candidate id']}))
    ids = proposal.get('group_ids')
    valid = {choice['id']: key for choice, key in zip(choices, keys)}
    if (proposal.get('source_sku') != source_sku or not isinstance(ids, list) or not 1 <= len(ids) <= 3
            or any(not isinstance(v, str) or v not in valid for v in ids)):
        raise ListingAIError('AI 未选出当前店铺的有效类目分组，请搜索并手动选择商品类型')
    result = [option for key in dict.fromkeys(valid[v] for v in ids) for option in groups[key]]
    if not fits(result):
        raise ListingAIError('所选类目分组过大，自动检索未完成，请搜索并手动选择商品类型')
    return result
