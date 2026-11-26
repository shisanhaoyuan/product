"""Amazon prelisting uses the captured reference price or verified ordinary offer."""
from .amazon_markets import source_market, market_config, location_matches
import json
import re
from decimal import ROUND_FLOOR, Decimal, InvalidOperation


def selling_price(basis, multiplier):
    """Automatic Amazon prices keep the multiplied dollar part and end in .99."""
    return (basis * multiplier).to_integral_value(rounding=ROUND_FLOOR) + Decimal('.99')


def regular_price_basis(facts):
    """No usable reference: retain the uniquely captured ordinary item price."""
    return (facts.get('original_price') in (None, '') and facts.get('currency') in ('USD','CAD')
            and not facts.get('offer_ambiguous')
            and (facts.get('original_price_state') in ('not_displayed', 'ambiguous')
                 or (not facts.get('original_price_state') and facts.get('status') == 'captured'))
            and facts.get('offer_scope', '').startswith(('main_offer', 'sellers_on_amazon_list:')))


def price_plan(db, listing_id, product, variant, multiplier):
    from .prelisting import money
    market=source_market(product['source'])
    config=market_config(market)
    asin = variant.get('asin')
    child = bool(product.get('variants'))
    sku = variant['sku'] if child else ''
    result = dict(base=None, landed=None, original=None, price=None, source_price=None, error='',
                  price_basis='amazon_captured_original_v1', capture_required=False,
                  capture_sku=sku)
    try:
        if not asin or not re.fullmatch('[A-Z0-9]{10}', asin):
            raise ValueError('缺少明确的 Amazon ASIN，无法采集原价')
        scopes = [sku]
        # Parent observations apply only to the one child with exactly this ASIN.
        if child and product.get('asin') == asin and sum(v.get('asin') == asin for v in product['variants']) == 1:
            scopes.append('')
        marks = ','.join('?' for _ in scopes)
        capture = db.execute(f'''SELECT data_json FROM source_captures
            WHERE listing_id=? AND asin=? AND variant_sku IN ({marks})
            ORDER BY captured_at DESC,id DESC LIMIT 1''', (listing_id, asin, *scopes)).fetchone()
        facts = json.loads(capture[0]) if capture else variant.get('source_facts') or {}
        if facts.get('asin') and facts['asin'] != asin:
            raise ValueError('来源 ASIN 与当前商品不一致，请重新采集')
        if capture and not location_matches(facts.get('delivery_location'),market):
            raise ValueError(f'来源配送地区尚未核验为 {config["postal_code"]}，请重新采集')
        if facts.get('currency') not in (None, '', config['currency']) or facts.get('offer_ambiguous'):
            raise ValueError('来源报价币种或报价归属异常，请重新采集')
        try:
            result['source_price'] = f"{money(facts.get('price')):.2f}"
        except (ValueError, InvalidOperation):
            pass
        ordinary = regular_price_basis(facts)
        if facts.get('original_price') in (None, '') and not ordinary:
            raise ValueError('缺少来源采集原价，请采集卖家与报价后重新计算')
        original = money(facts.get('price') if ordinary else facts['original_price'])
        final = selling_price(original, multiplier)
        money(final)
        result.update(base=f'{original:.2f}', original=f'{original:.2f}', price=f'{final:.2f}')
        if ordinary:
            result.update(original=None, price_basis='amazon_regular_no_reference_v1')
        # Landed cost is informational; shipping is not added to the pricing base.
        try:
            landed = money(facts.get('price')) + money(facts.get('shipping'), zero=True)
            result['landed'] = f'{landed:.2f}'
        except (ValueError, InvalidOperation):
            pass
    except (ValueError, InvalidOperation) as exc:
        result.update(error=str(exc), capture_required=bool(asin and re.fullmatch('[A-Z0-9]{10}', asin)))
    return result
