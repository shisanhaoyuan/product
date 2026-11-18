"""Schema-bounded defaults explicitly authorized by the user's listing Skill.

These are business rules, never supplier evidence or model confidence scores.
Ordinary defaults fill missing values. The fixed China origin rule is an
explicit exception, applied separately within the actual schema.
"""
import copy
import json
import re

from .walmart_listing_schema import plain, precise_validator


def blank(value):
    if value is None or isinstance(value, str) and not plain(value):
        return True
    return isinstance(value, (dict, list)) and all(blank(v) for v in
        (value.values() if isinstance(value, dict) else value))


def compact(value):
    return re.sub('[^a-z0-9]', '', str(value).lower())


FIXED = {
    'isfragile': 'No', 'isprop65warningrequired': 'No',
    'hasnrtllistingcertification': 'No', 'haswrittenwarranty': 'No',
    'condition': 'New', 'isassemblyrequired': 'No',
    'fulfillmentlagtime': 1, 'lagtime': 1,
    'countryoforiginsubstantialtransformation': 'China',
    'countryoforigintextiles': 'Imported', 'statechemicaldisclosure': '无',
    'adjustableheight': 'No', 'isadjustableheight': 'No',
    'powersource': '无',
}


def name(key, schema):
    return compact(key) if compact(key) in FIXED else compact(schema.get('title', key))


def has_default(key, schema):
    return (name(key, schema) in FIXED or compact(key) in
            {'agegroup', 'smallpartswarnings', 'smallpartswarningcode', 'netcontent'})


def adapt(schema, value):
    if schema.get('type') == 'array':
        values = value if isinstance(value, list) else [value]
        items = [adapt(schema.get('items', {}), v) for v in values]
        value = items if all(v is not None for v in items) else None
    elif 'enum' in schema:
        matches = [v for v in schema['enum'] if str(v).casefold() == str(value).casefold()]
        value = matches[0] if len(matches) == 1 else None
    return value if value is not None and precise_validator(schema).is_valid(value) else None


def skill_default(key, schema, current, facts):
    """Return a complete valid replacement and an honest user-rule explanation."""
    token = compact(key)
    value = None
    reason = 'user_authorized_rule：缺失字段按 digit-emperor-listing Skill 默认值填写'
    if token == 'netcontent':
        props = schema.get('properties', {})
        unit = next((k for k in ('productNetContentUnit', 'unit') if k in props), None)
        measure = next((k for k in ('productNetContentMeasure', 'measure') if k in props), None)
        if not unit or not measure or current is not None and not isinstance(current, dict):
            return None, ''
        value = copy.deepcopy(current or {})
        options = props[unit].get('enum', [])
        selected = value.get(unit)
        if blank(selected) or selected in ('请选择', 'Each') and selected not in options:
            value[unit] = next((v for v in ('Each', 'Case') if v in options), None)
        if blank(value.get(measure)):
            value[measure] = 1
        reason += '；净内容优先 Each、缺少时选实际 Case，保留已有数量和其他有效单位，不换算'
    elif not blank(current):
        return None, ''
    elif token == 'agegroup':
        value = 'Adult'
        evidence = json.dumps(facts, ensure_ascii=False)
        if schema.get('type') == 'array' and re.search(
                r'\b(?:suitable|appropriate|designed) for all ages\b', evidence, re.I):
            value = [v for v in schema.get('items', {}).get('enum', [])
                     if isinstance(v, str) and v.strip() and
                     not re.search(r'[,/;|]|请选择|select|choose', v, re.I)]
            reason += '；同 SKU 有明确全年龄证据，使用当前 Schema 的单项年龄选项'
    elif token in ('smallpartswarnings', 'smallpartswarningcode'):
        # Different official versions encode the same warning as text or 0.
        for option in ('0 - No warning applicable', '0', 0):
            value = adapt(schema, option)
            if value is not None:
                break
    else:
        value = FIXED.get(name(key, schema))
    return adapt(schema, value), reason


def length_fallback(key, schema, current):
    """22 inches is a last resort for lengths only, after source and AI attempts."""
    props = schema.get('properties', {})
    if (not re.search(r'length|width|height|depth|diameter|thickness', key, re.I)
            or re.search(r'weight|capacity|load', key, re.I)
            or not {'measure', 'unit'} <= props.keys()
            or current is not None and not isinstance(current, dict)):
        return None
    value = copy.deepcopy(current or {})
    # A number without a unit cannot be relabelled as inches safely.
    if not blank(value.get('measure')):
        return None
    unit = value.get('unit')
    if blank(unit):
        unit = next((u for u in props['unit'].get('enum', [])
                     if str(u).lower() in ('in', 'inch', 'inches')), None)
    if str(unit).lower() not in ('in', 'inch', 'inches'):
        return None
    value.update(unit=unit, measure=22)
    return adapt(schema, value)


ORIGIN_KEY = 'countryoforiginsubstantialtransformation'


def fixed_origin(key, schema):
    return name(key, schema) == ORIGIN_KEY


def enforce_origin(row, spec, audit=None):
    """User-selected listing origin; never overwrite the supplier source facts."""
    errors = []
    for section in ('visible', 'orderable'):
        for key, shape in spec.get(section, {}).get('properties', {}).items():
            if not fixed_origin(key, shape):
                continue
            value = adapt(shape, 'China')
            path = section+'.'+key
            if value is None:
                errors.append(dict(path=path, message='原产国固定规则需要 China，但当前沃尔玛字段不接受此选项'))
                continue
            previous = row[section].get(key)
            row[section][key] = value
            if audit is not None:
                audit[path] = dict(kind='user_rule', value=value, previous_value=previous, refs=[],
                    reason='user_authorized_rule：原产国（实质性转变）固定 China，覆盖旧值；不是供应商来源事实，不由 AI 推测')
    return errors
