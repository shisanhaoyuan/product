"""Saleyee source pricing/stock rules, shared by preview and stock publication."""
from decimal import Decimal, ROUND_HALF_UP
from .saleyee import SyncInput, summarize


def prepare(item, source, multiplier):
    raw = (source.get('source_facts') or {}).get('saleyee') or {}
    detail, price, stock = (raw.get(k) or {} for k in ('detail','price','inventory'))
    valid = source.get('source') == 'Saleyee' and source.get('sku') == item['sku'] and all(
        r.get('Sku') == item['sku'] for r in (detail,price,stock))
    errors = []
    if not valid:
        errors.append('赛盈 SKU 与商品详情、价格或库存不一致，请重新同步')
    if item.get('variants'):
        errors.append('赛盈按供应商 SKU 独立上架，不将组合配件当作子变体')
    if item.get('local_edit', {}).get('conflicts'):
        errors.append('本地编辑与最新来源有冲突，请先处理')
    info = summarize(detail, price, stock, SyncInput(**(raw.get('options') or {})))
    errors.extend(info['warnings'])
    base = info['landed'] if valid else None
    final = None
    if base is None or Decimal(base) <= 0:
        errors.append('缺少有效的 USD 采购价，不能计算上架售价')
    else:
        final = format((Decimal(base)*multiplier).quantize(Decimal('.01'),rounding=ROUND_HALF_UP),'f')
    quantity = info['inventory'] if valid else None
    inventory = None if quantity is None else ('10' if quantity >= 10 else '0')
    if inventory == '0':
        errors.append(f'赛盈可用库存为 {quantity}，按规则上架库存为 0')
    if detail.get('Published') is not True or detail.get('IsProductAuth') is not True:
        errors.append('赛盈商品未上架或尚未授权分销')
    tort = detail.get('TortInfo') or {}
    if tort.get('TortStatus') in (0, 1):
        errors.append('赛盈商品侵权状态待审核或已标记侵权，请核对授权后再提交')
    if type(detail.get('DistributionLimit')) is not int or detail['DistributionLimit'] not in (0, 1):
        errors.append('赛盈未返回明确分销权限，请重新同步确认')
    restrictions = detail.get('GoodsDistributionPlatformDetailList') or []
    if detail.get('DistributionLimit') == 0 and (not restrictions or any(
        'walmart' in str(v).casefold() or '沃尔玛' in str(v) for v in restrictions)):
        errors.append('赛盈限制此商品分销，不能提交沃尔玛')
    content = dict(images=item['images'],attributes=item['fields'].get('Saleyee attributes') or {},
        ean=item.get('ean',''),product_id_type='EAN',swatch_attribute='',swatch_image='',
        shipping_weight='',copy=item['copy'],source_url=item['source_url'],seller=item['seller'])
    variant = dict(sku=item['sku'],asin='',current_price=item['price'],stock=inventory,
        source_stock=quantity,unit_price=info['price'],shipping_fee=info['shipping'],currency=info['currency'],
        base=base,landed=base,original=None,price=final,error='；'.join(errors),content=content,
        source_facts=source['source_facts'],price_rule='saleyee_purchase_price_v2')
    return [variant],errors,['价格与库存取同站点、同仓库的赛盈 API 快照；采购价直接乘本批次倍率，不另加运费']
