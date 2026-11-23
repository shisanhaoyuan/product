"""Editable MSRP suggestions; never a validation constraint or AI decision."""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR


def suggested_msrp(price):
    try:
        value = Decimal(str(price))
        if not value.is_finite() or value <= 0:
            return None
        return (value * 2).to_integral_value(rounding=ROUND_FLOOR) + Decimal('0.99')
    except (InvalidOperation, ValueError, TypeError):
        return None


def fill_msrp(orderable):
    # Zero and other saved values are deliberate edits, subject only to schema
    # validation. Reopening, generation and source refresh must preserve them.
    if orderable.get('msrp') not in (None, ''):
        return False
    value = suggested_msrp(orderable.get('price'))
    if value is None:
        return False
    orderable['msrp'] = float(value)
    return True
