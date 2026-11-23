"""Display-price and availability filters shared by every product library."""
import json
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field, model_validator

from .giga_presentation import summary, number, money


class ProductRanges(BaseModel):
    stock_min: int | None = Field(default=None, ge=0, le=2147483647)
    stock_max: int | None = Field(default=None, ge=0, le=2147483647)
    price_min: Decimal | None = Field(default=None, ge=0, le=Decimal('9999999999.99'), max_digits=12, decimal_places=2)
    price_max: Decimal | None = Field(default=None, ge=0, le=Decimal('9999999999.99'), max_digits=12, decimal_places=2)

    @model_validator(mode='after')
    def valid_ranges(self):
        for name, label in (('stock', '库存'), ('price', '价格')):
            lower, upper = getattr(self, name + '_min'), getattr(self, name + '_max')
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(label + '下限不能大于上限')
        return self


def quantity(value):
    result = number(value)
    return int(result) if result is not None and result == result.to_integral_value() else None


def metrics(data):
    """One candidate per child; GIGA prices use the exact displayed total range.

    Supplier stock deliberately uses upstream availability, unaffected by ERP edits.
    Unknown/invalid data remains null, never zero or an invented price.
    """
    facts = data.get('source_facts') or {}
    if data.get('source') in ('GigaB2B', 'GigaB2B_CA'):
        info = summary(facts)
        totals = info['estimated_totals']
        return [dict(stock=info['inventory'], price_low=totals[0] if totals else None,
                     price_high=totals[-1] if totals else None)]
    rows = data.get('variants') or [data]
    result = []
    for row in rows:
        price = number(row.get('price'))
        try:
            price = money(price)
        except InvalidOperation:
            price = None
        stock = facts.get('inventory') if data.get('source') == 'Saleyee' else row.get('stock')
        result.append(dict(stock=quantity(stock), price_low=price, price_high=price))
    return result


def encoded_metrics(raw):
    return json.dumps(metrics(json.loads(raw)), separators=(',', ':'))


def clause(*, postgres=False, inventory_status='', inventory_states=(),
           stock_min=None, stock_max=None, price_min=None, price_max=None):
    from .product_filters import json_text
    conditions, args = [], []
    def value(name):
        return f"CAST({json_text('metric.value', name, postgres=postgres)} AS NUMERIC)"
    for states in ([inventory_status] if inventory_status else [], inventory_states):
        if states:
            conditions.append('(' + ' OR '.join(value('stock') + ('>0' if state == 'in_stock' else '=0') for state in states) + ')')
    # A displayed interval matches when it overlaps the requested inclusive range.
    for name, operator, bound in (('stock', '>=', stock_min), ('stock', '<=', stock_max),
                                  ('price_high', '>=', price_min), ('price_low', '<=', price_max)):
        if bound is not None:
            conditions.append(value(name) + operator + 'CAST(? AS NUMERIC)')
            args.append(str(bound))
    if not conditions:
        return '', []
    rows = ('jsonb_array_elements(listings.filter_metrics_json::jsonb) AS metric(value)' if postgres else
            'json_each(product_filter_metrics(listings.data_json)) AS metric')
    return f"EXISTS (SELECT 1 FROM {rows} WHERE {' AND '.join(conditions)})", args


def initialize(db, postgres=False):
    if db.execute('SELECT 1 FROM migrations WHERE version=25').fetchone():
        return
    if postgres:
        from .projection import refresh
        for row in db.execute('SELECT id FROM listings').fetchall():
            refresh(db, row['id'])
    db.execute('INSERT INTO migrations VALUES(25) ON CONFLICT DO NOTHING')
