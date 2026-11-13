"""Derived GIGA display facts; never rewrite source prices or listing rules."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def number(value):
    if value is None or isinstance(value, bool) or not str(value).strip():
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def money(value):
    return None if value is None else format(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP), 'f')


def obj(value):
    return value if isinstance(value, dict) else {}


def high(value):
    return str(value or '').strip().casefold() in {'high', '高'}


def summary(facts):
    giga = obj(obj(facts).get('giga'))
    price = obj(giga.get('price'))
    detail = obj(giga.get('detail'))
    inventory = obj(giga.get('inventory'))
    seller_inventory = obj(inventory.get('sellerInventoryInfo'))
    # The documented fee includes shipping and packing, so never add packing twice.
    unit = number(price.get('price'))
    fee = number(price.get('shippingFee'))
    fee_range = obj(price.get('shippingFeeRange'))
    low, high_fee = number(fee_range.get('minAmount')), number(fee_range.get('maxAmount'))
    currency = price.get('currency', '').strip() if isinstance(price.get('currency'), str) else ''
    totals = []
    note = '随机仓库 · 单价＋普通地区物流费（含打包费）'
    if unit is not None and currency:
        range_present = any(v not in (None, '') for v in fee_range.values())
        if range_present and (low is None or high_fee is None or low > high_fee):
            note = '物流费区间不完整或异常，需核对后确认总额'
        elif low is not None and high_fee is not None and low != high_fee:
            totals = [money(unit + low), money(unit + high_fee)]
            note = '物流费存在区间，展示预计总额范围；实际按配送条件确认'
        elif fee is not None:
            totals = [money(unit + fee)]
        elif low is not None and low == high_fee:
            totals = [money(unit + low)]
        else:
            note = '物流费未返回，无法计算含打包费总额'
    else:
        note = '商品单价或币种未返回，无法计算预计总额'
    seller = {**obj(price.get('sellerInfo')), **{k: v for k, v in obj(detail.get('sellerInfo')).items() if v not in (None, '')}}
    score = number(seller.get('gigaIndex'))
    quantity = seller_inventory.get('sellerAvailableInventory')
    quantity = quantity if type(quantity) is int and quantity >= 0 else None
    risks = []
    if quantity == 0:
        risks.append('无可售库存')
    if score is not None and score < 80:
        risks.append('卖家评分低于 80')
    if high(detail.get('returnRate')):
        risks.append('商品退货率高')
    if high(seller.get('sellerReturnRate')):
        risks.append('卖家退货率高')
    return dict(estimated_totals=totals, unit_price=money(unit), shipping_fee=money(fee),
                currency=currency, price_note=note, inventory=quantity,
                giga_index=str(score) if score is not None else None,
                low_score_threshold=80, seller=seller, product_return_rate=detail.get('returnRate'),
                risks=risks)
