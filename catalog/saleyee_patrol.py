"""Refresh only the price and inventory of an imported Saleyee SKU."""
import time

from .saleyee import SaleyeeError, SyncInput, index_rows, summarize


def quote_key(facts):
    offer = facts.get('selected_offer') or {}
    values = [offer.get(k) for k in ('Store', 'StockCode', 'LogisticsProductCode')]
    return values if all(isinstance(v, str) and v.strip() for v in values) else None


def fetch_rows(client, operation, skus):
    body = {'SkuList': skus}
    if operation == 'stock':
        body.update(PageIndex=1, PageSize=30)
    try:
        client.deadline = time.monotonic() + 60
        rows = index_rows(client.query(operation, body))
        if rows.keys() - set(skus):
            raise SaleyeeError('SKU 不匹配')
        return rows, ''
    except (TypeError, ValueError, KeyError):
        label = '价格' if operation == 'price' else '库存'
        return {}, f'赛盈{label}查询失败或响应异常，保留原值'


def refresh(data, price, stock, price_error, stock_error):
    facts = data['source_facts']
    raw = facts['saleyee']
    sku = data['sku']
    if any((raw.get(k) or {}).get('Sku') != sku for k in ('detail', 'price', 'inventory')):
        return False, False, ['来源 SKU 与已保存快照不符，未更新']
    options = SyncInput(**raw.get('options', {}))
    pinned = quote_key(facts)
    if pinned:
        options.quote_site, options.warehouse, options.logistics = pinned
    elif facts.get('selected_offer'):
        return False, False, ['原报价缺少站点、仓库或物流方式，请重新选择报价']

    def resolve(price_row, stock_row):
        try:
            info = summarize(raw['detail'], price_row, stock_row, options)
            key = quote_key(info)
            if key and (not pinned or key == pinned):
                return info
        except (ValueError, TypeError, AttributeError, KeyError):
            pass
        return {}

    # A cached counterpart identifies the quote only. It cannot certify freshness.
    # Evaluate stock separately so a malformed price cannot erase good inventory.
    stock_info = resolve(raw['price'], stock) if stock is not None else {}
    price_info = resolve(price, stock or raw['inventory']) if price is not None else {}
    if not price_info and price is not None and not stock_info:
        # Malformed/missing stock may not invalidate an independently proven US price.
        price_info = resolve(price, {'Sku': sku})
    price_ok = price_info.get('price') is not None
    if stock is not None and price_ok and price_info.get('inventory') is not None:
        stock_info = price_info
    stock_ok = stock_info.get('inventory') is not None
    if not pinned and not price_ok:
        stock_ok = False
    errors = []
    if price_ok:
        for key in ('price', 'selling_price', 'original_price', 'currency', 'shipping',
                    'shipping_origin', 'landed', 'selected_offer', 'offers', 'available_offers'):
            facts[key] = price_info[key]
        data['price'] = facts['price']
        raw['price'] = price
        # Keep subsequent previews/manual refreshes on the same selected quote.
        site, warehouse, logistics = quote_key(price_info)
        raw.setdefault('options', {}).update(quote_site=site, warehouse=warehouse, logistics=logistics)
    else:
        errors.append(price_error or '没有匹配该商品的有效美国仓 USD 报价，保留原价格')
    if stock_ok:
        facts['inventory'] = stock_info['inventory']
        facts['warehouses'] = stock_info['warehouses']
        facts['shipping_addresses'] = stock_info['shipping_addresses']
        facts['return_addresses'] = stock_info['return_addresses']
        raw['inventory'] = stock
        data['stock'] = str(facts['inventory'])
    else:
        errors.append(stock_error or '未返回已选站点和仓库的有效库存，保留原库存')
    if price_ok or stock_ok:
        info = resolve(raw['price'], raw['inventory'])
        facts['warnings'] = info.get('warnings', []) + errors
    return price_ok, stock_ok, errors
