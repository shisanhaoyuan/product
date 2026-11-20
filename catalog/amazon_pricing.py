"""Amazon prelisting uses the captured original price, never a marked-up ERP price."""
import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def price_plan(db, listing_id, product, variant, multiplier):
    from .prelisting import money
    asin = variant.get('asin')
    child = bool(product.get('variants'))
    sku = variant['sku'] if child else ''
    result = dict(base=None, landed=None, original=None, price=None, error='',
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
        if capture and not re.search(r'\b10001\b', facts.get('delivery_location') or ''):
            raise ValueError('来源配送地区尚未核验为 10001，请重新采集')
        if facts.get('currency') not in (None, '', 'USD') or facts.get('offer_ambiguous'):
            raise ValueError('来源报价币种或报价归属异常，请重新采集')
        if facts.get('original_price') in (None, ''):
            raise ValueError('缺少来源采集原价，请采集卖家与报价后重新计算')
        original = money(facts['original_price'])
        final = (original * multiplier).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        money(final)
        result.update(base=f'{original:.2f}', original=f'{original:.2f}', price=f'{final:.2f}')
        # Informational only: shipping/current offer never changes this pricing base.
        try:
            landed = money(facts.get('price')) + money(facts.get('shipping'), zero=True)
            result['landed'] = f'{landed:.2f}'
        except (ValueError, InvalidOperation):
            pass
    except (ValueError, InvalidOperation) as exc:
        result.update(error=str(exc), capture_required=bool(asin and re.fullmatch('[A-Z0-9]{10}', asin)))
    return result
