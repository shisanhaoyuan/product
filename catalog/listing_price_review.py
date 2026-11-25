"""Human acknowledgement of missing Amazon source price, scoped to one listing price."""
import json
from decimal import Decimal, InvalidOperation

from .prelisting import fingerprint

ACTION = 'verify_source_price'
MISSING = '缺少来源采集原价，请采集卖家与报价后重新计算'


def eligible(item, variant):
    return item.get('source') in ('Amazon', 'Amazon_CA') and variant.get('price') is None and variant.get('error') == MISSING


def review_key(basis, item, variant, row):
    try:
        price = Decimal(str(row.get('orderable', {}).get('price')))
        if not price.is_finite() or price <= 0:
            return ''
    except (ValueError, InvalidOperation):
        return ''
    return fingerprint(dict(store_id=basis['store']['id'], brand=basis['brand'],
        listing_id=row['listing_id'], source_sku=row['source_sku'], platform_sku=row['platform_sku'],
        source_digest=item['source_digest'], variant=variant, price=str(price.normalize())))


def reviews(db, doc):
    basis=json.loads(doc['basis_json'])
    entries={v['platform_sku']:(i,v) for i in basis['items'] for v in i['variants']}
    rows={r['platform_sku']:r for r in json.loads(doc['rows_json'])}
    result={}
    for event in db.execute('SELECT actor_id,at,details_json FROM walmart_listing_events WHERE batch_id=? AND action=? ORDER BY at DESC,id DESC',
                            (doc['batch_id'],ACTION)):
        data=json.loads(event['details_json']);sku=data.get('platform_sku')
        if sku in result or sku not in rows or sku not in entries:
            continue
        item,variant=entries[sku]
        if eligible(item,variant) and data.get('key') == review_key(basis,item,variant,rows[sku]):
            result[sku]=dict(at=event['at'],actor_id=event['actor_id'],price=data['price'],
                **{k:data[k] for k in ('original_price','multiplier','method') if k in data})
    return result
