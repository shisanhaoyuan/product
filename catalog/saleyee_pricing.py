"""Resolve legacy unselected quotes from saved evidence, before manual overlays."""


def resolve_saved_quote(data):
    if data.get('source') != 'Saleyee':
        return
    facts = data.get('source_facts') or {}
    if facts.get('selected_offer'):
        return
    raw = facts.get('saleyee') or {}
    sku = data.get('sku')
    if not sku or any(not isinstance(raw.get(k), dict) or raw[k].get('Sku') != sku
                      for k in ('detail', 'price', 'inventory')):
        return
    from .saleyee import summarize, SyncInput
    try:
        info = summarize(raw['detail'], raw['price'], raw['inventory'], SyncInput(**(raw.get('options') or {})))
    except (ValueError, TypeError, KeyError, AttributeError):
        return
    if not info['selected_offer'] or info['price'] is None:
        return
    facts.update(info)
    facts['quote_resolution'] = 'saved_api_default_v1'
    data['source_facts'] = facts
    data['price'] = info['price']
    data['stock'] = None if info['inventory'] is None else str(info['inventory'])


def initialize(db, postgres=False):
    """Refresh only Saleyee's derived read model, leaving source evidence intact."""
    if db.execute('SELECT 1 FROM migrations WHERE version=23').fetchone():
        return
    if postgres:
        from .projection import refresh
        rows = db.execute("SELECT l.id FROM listings l JOIN products p ON p.id=l.product_id WHERE p.source='Saleyee'").fetchall()
        for row in rows:
            refresh(db, row['id'])
    db.execute('INSERT INTO migrations VALUES(23) ON CONFLICT DO NOTHING')
