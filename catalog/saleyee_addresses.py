"""All product-scoped supplier addresses, independent of the selected price route."""
import hashlib
import json


def text(value):
    return ' '.join(value.split()) if isinstance(value, str) else ''


def address(row, *, inventory=False, returning=False, site='', warehouse=''):
    fields = (('CountryCode', 'Province', 'City', 'Address1', 'Address2', 'PostCode') if returning else
              ('StockCountryCode', 'StockProvince', 'StockCity', 'StockAddress', 'StockAddressTwo', 'StockPostCode'))
    parts = [text(row.get(k)) for k in fields]
    # A warehouse name/country alone is not a street address filter.
    key = hashlib.sha256(json.dumps([p.casefold() for p in parts], ensure_ascii=False).encode()).hexdigest() if parts[3] or parts[5] else ''
    quantity = row.get('Qty') if inventory else None
    return dict(key=key, address=' '.join(p for p in parts if p), country=parts[0],
        code=text(row.get('PlatformStockAbbr') or row.get('PlatformStockCode') or row.get('ThirdpartyStockCode') or row.get('WarehouseCode')),
        name=text(row.get('PlatformStockName') or row.get('ThirdpartyStockName') or row.get('WarehouseName')),
        warehouse_code=warehouse or text(row.get('WarehouseCode')), site=site,
        quantity=quantity if type(quantity) is int and quantity >= 0 else None)


def summarize_addresses(detail, stock):
    shipping, returns = [], []
    for site in stock.get('ProductInventorySiteList') or []:
        for region in site.get('ProductInventoryList') or []:
            for row in region.get('StockItemList') or []:
                shipping.append(address(row, inventory=True, site=text(site.get('SiteHosts')),
                                        warehouse=text(region.get('StockCode'))))
    for row in detail.get('ProductSendAddressList') or []:
        item = address(row)
        if item['key'] and any(v['key'] == item['key'] for v in shipping):
            continue  # Same physical address: retain the precise per-warehouse inventory row.
        shipping.append(item)
    for row in detail.get('ProductReturnAddressList') or []:
        returns.append(address(row, returning=True))
    def unique(values):
        found = {}
        for item in values:
            found.setdefault((item['key'], item['site'], item['warehouse_code'], item['code']), item)
        return list(found.values())
    return dict(shipping_addresses=unique(shipping), return_addresses=unique(returns))


def resolve_saved_addresses(data):
    if data.get('source') != 'Saleyee':
        return
    facts = data.setdefault('source_facts', {})
    raw = facts.get('saleyee') or {}
    detail, stock = raw.get('detail') or {}, raw.get('inventory') or {}
    if detail.get('Sku') != data.get('sku') or stock.get('Sku') != data.get('sku'):
        return
    facts.update(summarize_addresses(detail, stock))


def address_rows(relation, kind, postgres=False):
    assert kind in ('shipping_addresses', 'return_addresses')
    if postgres:
        return f"{relation} CROSS JOIN LATERAL jsonb_array_elements(COALESCE(scoped.data_json::jsonb #> '{{source_facts,{kind}}}', '[]'::jsonb)) address"
    return f"{relation} CROSS JOIN json_each(scoped.data_json, '$.source_facts.{kind}') address"


def address_clause(kind, postgres=False):
    from .product_filters import json_text
    rows = address_rows('(SELECT listings.data_json) scoped', kind, postgres)
    return f"EXISTS (SELECT 1 FROM {rows} WHERE {json_text('address.value', 'key', postgres=postgres)}=?)"


def address_options(db, relation, args, postgres=False):
    from .product_filters import json_text
    result = {}
    for kind in ('shipping_addresses', 'return_addresses'):
        key = json_text('address.value', 'key', postgres=postgres)
        label = json_text('address.value', 'address', postgres=postgres)
        result[kind] = [dict(row) for row in db.execute(
            f"SELECT {key} AS value, MIN({label}) AS label, COUNT(DISTINCT scoped.id) AS count "
            f"FROM {address_rows(relation + ' scoped', kind, postgres)} WHERE {key}<>'' "
            f"GROUP BY {key} ORDER BY MIN({label})", args)]
    return result


def initialize(db, postgres=False):
    if db.execute('SELECT 1 FROM migrations WHERE version=24').fetchone():
        return
    if postgres:
        from .projection import refresh
        for row in db.execute("SELECT l.id FROM listings l JOIN products p ON p.id=l.product_id WHERE p.source='Saleyee'").fetchall():
            refresh(db, row['id'])
    db.execute('INSERT INTO migrations VALUES(24) ON CONFLICT DO NOTHING')
