"""Fill verified source facts into the local overlay, never rewrite ERP evidence."""
import json
import re
from datetime import datetime, timezone


ALIASES = {
    'seller': {'seller', 'seller_name', 'sold_by', 'merchant_name', '商品卖家', '卖家'},
    'shipping': {'source_shipping', 'amazon_shipping', '来源运费'},
    'delivery': {'delivery', 'delivery_time', 'source_delivery', '配送时间', '物流时间'},
    'price': {'source_price', 'amazon_price', '来源价格'},
    'original_price': {'source_original_price', '来源原价'},
}


def empty(value):
    if value is None or value == []:
        return True
    if isinstance(value, list):
        return all(empty(item) for item in value)
    if isinstance(value, dict) and set(value) == {'val'}:
        return empty(value['val'])
    return isinstance(value, str) and re.sub(r'<[^>]*>', '', value).strip().casefold() in {
        '', '未采集', '未填写', '未记录', '未核验', '请选择', '--', 'none', 'null',
    }


def filled_value(previous, value):
    if isinstance(previous, dict):
        return {'val': filled_value(previous['val'], value)}
    return [value] if isinstance(previous, list) else value


def sync_capture(catalog, db, lid, sku, asin, result, actor_id):
    from .editing import encoded, local_product, value_at
    result = dict(result)
    if not re.search(r'\b10001\b', result.get('delivery_location') or ''):
        for key in ('price', 'shipping', 'delivery', 'original_price', 'basis_price', 'currency'):
            result.pop(key, None)
    row = db.execute('SELECT * FROM listings WHERE id=?', (lid,)).fetchone()
    old = db.execute('SELECT * FROM product_edits WHERE listing_id=?', (lid,)).fetchone()
    version = old['revision'] if old else 0
    before = json.loads(old['changes_json']) if old else []
    current = json.loads(local_product(row['data_json'], encoded(before), version,
        old['updated_at'] if old else '', old['source_digest'] if old else '', row['digest']))
    target = current if not sku else next(v for v in current['variants'] if v['sku'] == sku)
    targets = [(sku or '', target)]
    # Share only an unambiguous parent/child identity, never all siblings of a parent.
    same = [v for v in current['variants'] if v['asin'] == asin]
    if current['asin'] == asin and len(same) == 1:
        targets = [('', current), (same[0]['sku'], same[0])]
    changes = list(before)
    filled = []
    preserved = []

    def add(target_sku, field, key, value):
        change = dict(variant_sku=target_sku, field=field, key=key, value=value,
                      capture_asin=asin, capture_source=current['source'], captured_at=result['captured_at'])
        identity = lambda c: (c['variant_sku'], c['field'], c['key'])
        # A deliberately edited blank is also a user decision: don't overwrite it.
        prior = next((c for c in changes if identity(c) == identity(change)), None)
        if prior and not prior.get('capture_asin'):
            preserved.append(f'{target_sku or "主商品"}:{key or field}')
            return
        changes[:] = [c for c in changes if identity(c) != identity(change)]
        changes.append(change)
        dest, dest_key = value_at(current, change)
        dest[dest_key] = value
        filled.append(f'{target_sku or "主商品"}:{key or field}')

    for target_sku, item in targets:
        seller = result.get('seller')
        if not empty(seller):
            if empty(item.get('seller')):
                add(target_sku, 'seller', '', seller)
            elif item['seller'] != seller:
                preserved.append(f'{target_sku or "主商品"}:seller')
        # Source facts are explicitly source values, not the listing's multiplied price.
        facts = item.setdefault('source_facts', {})
        for key in ('seller', 'price', 'shipping', 'delivery', 'original_price', 'basis_price', 'currency'):
            value = result.get(key)
            if not empty(value) and empty(facts.get(key)):
                add(target_sku, 'source_facts', key, value)
        container = 'attributes' if target_sku else 'fields'
        for key, previous in list(item[container].items()):
            canonical = next((name for name, names in ALIASES.items() if key.casefold() in names), None)
            value = item.get('seller') if canonical == 'seller' else result.get(canonical or '')
            if canonical and empty(previous) and not empty(value):
                add(target_sku, container, key, filled_value(previous, value))
        dynamic = item[container].get('dynamic_attributes')
        if isinstance(dynamic, dict):
            updated = dict(dynamic)
            for key, previous in dynamic.items():
                canonical = next((name for name, names in ALIASES.items() if key.casefold() in names), None)
                value = item.get('seller') if canonical == 'seller' else result.get(canonical or '')
                if canonical and empty(previous) and not empty(value):
                    updated[key] = filled_value(previous, value)
            if updated != dynamic:
                add(target_sku, container, 'dynamic_attributes', updated)
    if filled:
        changes = [c for c in changes if c['field'] != '_manual_review']
        now = datetime.now(timezone.utc).isoformat()
        db.execute('''INSERT INTO product_edits VALUES(?,?,?,?,?) ON CONFLICT(listing_id)
            DO UPDATE SET revision=excluded.revision,source_digest=excluded.source_digest,
                changes_json=excluded.changes_json,updated_at=excluded.updated_at''',
            (lid, version + 1, row['digest'], encoded(changes), now))
        db.execute('INSERT INTO product_edit_events(listing_id,at,actor_id,action,data_json) VALUES(?,?,?,?,?)',
            (lid, now, actor_id, 'source_sync', encoded({'before': before, 'after': changes,
                'asin': asin, 'variant_sku': sku or '', 'filled': filled})))
        catalog.refresh(db, lid)
    return dict(filled=filled, preserved=preserved)


def resolve_missing_issues(data):
    """Only retire explicit missing-fact checks; price mismatches and save checks remain."""
    resolved, pending = [], []
    for issue in data['issues']:
        field = str(issue.get('field_id') or issue.get('field') or issue.get('key') or '').casefold()
        canonical = next((key for key, aliases in ALIASES.items() if field in aliases), None)
        sku = issue.get('sku') or issue.get('variant_sku')
        candidates = [v for v in data['variants'] if v['sku'] == sku] if sku else [data]
        target = candidates[0] if len(candidates) == 1 else {}
        value = target.get('seller') if canonical == 'seller' else target.get('source_facts', {}).get(canonical)
        reasons = issue.get('errors', issue.get('reason', ''))
        reasons = reasons if isinstance(reasons, list) else [reasons]
        missing = all(str(reason).strip().casefold() in {
            '', 'missing', 'missing_value', 'required', 'empty', 'not_collected',
            'missing_seller', 'seller_missing', '未采集', '未填写', '未记录', '卖家未采集',
        } for reason in reasons)
        # An expected value / nonempty prior value indicates a mismatch, not a missing fact.
        missing = missing and not issue.get('expected') and empty(issue.get('actual'))
        if canonical and missing and not empty(value):
            resolved.append(issue)
        else:
            pending.append(issue)
    data['issues'] = pending
    data['resolved_issues'] = resolved
