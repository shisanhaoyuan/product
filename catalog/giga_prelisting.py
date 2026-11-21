"""Prepare GIGA source snapshots without applying Amazon/ERP evidence rules."""
import re
from decimal import Decimal, ROUND_HALF_UP

from .giga_presentation import summary, obj


def claim_identity(item):
    # The existing claim column is named asin; GIGA has no ASIN.
    return 'sku:' + item['sku'] if item['source'] in ('GigaB2B', 'GigaB2B_CA', 'Saleyee') else item['asin']


def has_text(value):
    import html
    return bool(html.unescape(re.sub('<[^>]*>', '', str(value or ''))).replace('\xa0', ' ').strip())


def prepare(item, source, multiplier):
    errors, warnings = [], ['使用大建云仓 API 来源快照；正式提交前需重新同步价格和库存，校验沃尔玛类目及必填项']
    facts = obj(source.get('source_facts'))
    giga = obj(facts.get('giga'))
    detail, price, inventory = (obj(giga.get(key)) for key in ('detail', 'price', 'inventory'))
    valid_identity = source.get('source') in ('GigaB2B', 'GigaB2B_CA') and source.get('sku') == item['sku'] and all(
        value.get('sku') == item['sku'] for value in (detail, price, inventory))
    if not valid_identity:
        errors.append('供应商 SKU 与价格、库存或详情来源不一致，请重新同步')
    if item.get('variants'):
        errors.append('云仓商品包含未支持的变体结构；组合配件不能自动当作子变体，请核对')
    if item.get('local_edit', {}).get('conflicts'):
        errors.append('本地编辑与最新来源存在冲突，请先处理')
    info = summary(facts)
    totals = info['estimated_totals']
    base = final = None
    expected_currency = 'CAD' if item['source'] == 'GigaB2B_CA' else 'USD'
    if info['currency'].upper() != expected_currency:
        errors.append(f'缺少 {expected_currency} 币种的来源价格，请重新同步')
    elif info['unit_price'] is None or Decimal(info['unit_price']) <= 0:
        errors.append('来源普通单价缺失或不是有效正数')
    elif len(totals) != 1:
        errors.append('物流费缺失、异常或存在区间，无法确定单一售价基准，请核对配送条件')
    elif Decimal(totals[0]) <= 0:
        errors.append('来源普通单价与物流费合计必须大于 0')
    elif valid_identity:
        base = totals[0]
        final = format((Decimal(base) * multiplier).quantize(Decimal('.01'), rounding=ROUND_HALF_UP), 'f')
    quantity = info['inventory'] if valid_identity else None
    stock = None if quantity is None else ('10' if quantity >= 10 else '0')
    if quantity is None:
        errors.append('Seller 可售库存缺失或异常，不能按 0 处理，请重新同步')
    elif stock == '0':
        errors.append(f'Seller 可售库存为 {quantity}，按规则上架库存为 0，请补货后重新同步')
    if detail.get('skuAvailable') is False:
        errors.append('供应商标记该 SKU 不可用，请先核对')
    for label, valid in (
        ('标题', has_text(item['copy']['title'])),
        ('描述', has_text(item['copy']['description'])),
        ('卖点', any(has_text(v) for v in item['copy']['features'])),
        ('商品图片', bool(item['images'])),
        ('UPC / GTIN（或后续平台豁免证明）', bool(item.get('ean'))),
    ):
        if not valid:
            errors.append(f'缺少{label}，请在商品档案补齐后重新预览')
    if not (item['fields'].get('GIGA category') or detail.get('categoryPath') or detail.get('category')):
        errors.append('来源类目缺失，请补齐后核对沃尔玛类目')
    warnings.extend(info['risks'])
    if item.get('local_edit', {}).get('changed'):
        warnings.append('本地文案等修改已纳入批次；价格和库存仍取原始 API 快照，不使用手工售价重复乘倍率')
    content = dict(images=item['images'], attributes=item['fields'].get('GIGA attributes') or {},
                   ean=item['ean'], product_id_type='GTIN' if item['ean'] else '',
                   swatch_attribute='', swatch_image='', shipping_weight='', copy=item['copy'],
                   source_url=item['source_url'], seller=item['seller'])
    variant = dict(sku=item['sku'], asin='', current_price=item['price'], stock=stock,
                   source_stock=quantity, unit_price=info['unit_price'], shipping_fee=info['shipping_fee'],
                   currency=info['currency'], base=base, landed=base, original=None, price=final,
                   error='；'.join(errors), content=content, source_facts=facts,
                   price_rule='giga_regular_landed_v1')
    return [variant], errors, warnings
