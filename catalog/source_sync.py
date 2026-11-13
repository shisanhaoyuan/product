"""Fill verified source facts into the local overlay, never rewrite ERP evidence."""
import json
import re
from html import unescape
from decimal import Decimal, InvalidOperation
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
    return isinstance(value, str) and unescape(re.sub(r'<[^>]*>', '', value)).strip().casefold() in {
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
    targets = [(sku or '', target, False)]
    # Share offer facts only with an unambiguous matching parent/child identity.
    same = [v for v in current['variants'] if v['asin'] == asin]
    if current['asin'] == asin and len(same) == 1:
        targets = [('', current, False), (same[0]['sku'], same[0], False)]
    # User-requested parent seller inheritance is a catalog default, not proof of
    # the seller on a different ASIN. Preserve that provenance and never inherit prices.
    if not sku and not empty(result.get('seller')):
        selected = {target_sku for target_sku, _, _ in targets}
        targets.extend((v['sku'], v, True) for v in current['variants'] if v['sku'] not in selected)
    changes = list(before)
    filled = []
    preserved = []

    def inherited(target_sku, field, key=''):
        return any(c.get('inherited_parent_seller') and c['variant_sku'] == target_sku
                   and c['field'] == field and c['key'] == key for c in changes)

    def add(target_sku, field, key, value):
        change = dict(variant_sku=target_sku, field=field, key=key, value=value,
                      capture_asin=asin, capture_source=current['source'], captured_at=result['captured_at'])
        if shared_seller:
            change.update(capture_target_asin=item['asin'], inherited_parent_seller=True)
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

    for target_sku, item, shared_seller in targets:
        seller = result.get('seller')
        if not empty(seller):
            if empty(item.get('seller')) or (not shared_seller and inherited(target_sku, 'seller')):
                add(target_sku, 'seller', '', seller)
            elif item['seller'] != seller:
                preserved.append(f'{target_sku or "主商品"}:seller')
        # Source facts are explicitly source values, not the listing's multiplied price.
        facts = item.setdefault('source_facts', {})
        keys = () if shared_seller else (
            'seller', 'price', 'shipping', 'delivery', 'original_price', 'basis_price', 'currency')
        for key in keys:
            value = result.get(key)
            if not empty(value) and empty(facts.get(key)):
                add(target_sku, 'source_facts', key, value)
        container = 'attributes' if target_sku else 'fields'
        for key, previous in list(item[container].items()):
            canonical = next((name for name, names in ALIASES.items() if key.casefold() in names), None)
            value = item.get('seller') if canonical == 'seller' else result.get(canonical or '')
            if shared_seller and canonical != 'seller':
                continue
            replace_inherited = not shared_seller and canonical == 'seller' and inherited(target_sku, container, key)
            if canonical and (empty(previous) or replace_inherited) and not empty(value):
                add(target_sku, container, key, filled_value(previous, value))
        dynamic = item[container].get('dynamic_attributes')
        if isinstance(dynamic, dict):
            updated = dict(dynamic)
            for key, previous in dynamic.items():
                canonical = next((name for name, names in ALIASES.items() if key.casefold() in names), None)
                value = item.get('seller') if canonical == 'seller' else result.get(canonical or '')
                if shared_seller and canonical != 'seller':
                    continue
                replace_inherited = not shared_seller and canonical == 'seller' and inherited(target_sku, container, 'dynamic_attributes')
                if canonical and (empty(previous) or replace_inherited) and not empty(value):
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
        details = issue.get('details') if isinstance(issue.get('details'), dict) else {}
        field = str(issue.get('field_id') or issue.get('field') or issue.get('key')
                    or details.get('field') or '').strip().lstrip('*').casefold()
        canonical = next((key for key, aliases in ALIASES.items() if field in aliases), None)
        if field == 'shipping_weight':
            canonical = field
        sku = issue.get('sku') or issue.get('variant_sku') or details.get('sku') or details.get('variant_sku')
        candidates = [v for v in data['variants'] if v['sku'] == sku] if sku else [data]
        if sku and not candidates:
            candidates = [v for v in data['variants'] if v.get('asin') == sku]
        target = candidates[0] if len(candidates) == 1 else {}
        value = target.get(canonical) if canonical in {'seller', 'shipping_weight'} else target.get('source_facts', {}).get(canonical)
        reasons = []
        for record in (issue, details):
            for key in ('reason', 'errors'):
                value_reason = record.get(key, '')
                reasons.extend(value_reason if isinstance(value_reason, list) else [value_reason])
        def missing_reason(reason):
            text = str(reason or '').strip().casefold()
            if not text:
                return True
            # Never mistake a mixed missing/mismatch error for a simple absent field.
            if re.search(r'mismatch|differ|conflict|invalid|ambiguous|未核验|不一致|冲突|无效|多个', text):
                return False
            return bool(re.fullmatch(r'(?:missing|empty|required|not[ _]collected|not[ _]recorded|not[ _]provided)(?:[ _].+)?', text)
                        or re.fullmatch(r'.+?(?:[ _](?:is[ _])?)?(?:missing|empty|not[ _]collected|not[ _]recorded|not[ _]provided)', text)
                        or re.fullmatch(r'(?:卖家|商品卖家|运输重量|配送时间|来源运费)?(?:信息)?(?:未采集|未填写|未记录|为空|缺失|缺少)(?:信息)?[。.!！]?', text)
                        or re.fullmatch(r'(?:缺少|缺失)(?:卖家|商品卖家|运输重量|配送时间|来源运费)(?:信息)?[。.!！]?', text))
        missing = all(missing_reason(reason) for reason in reasons)
        if issue.get('section') in {'save', 'save_confirmation', 'reopen', 'persistence', 'identity', 'pricing_checks', 'stock_checks'}:
            missing = False
        # An expected value / nonempty prior value indicates a mismatch, not a missing fact.
        missing = missing and all(empty(record.get('expected')) and empty(record.get('actual'))
                                  for record in (issue, details))
        if canonical == 'shipping_weight':
            try:
                weight = Decimal(str(value))
                missing = missing and weight.is_finite() and weight > 0
            except InvalidOperation:
                missing = False
        if canonical and missing and not empty(value):
            resolved.append(issue)
        else:
            pending.append(issue)
    data['issues'] = pending
    data['resolved_issues'] = resolved
