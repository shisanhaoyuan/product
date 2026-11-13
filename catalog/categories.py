"""Read category from the recorded ERP Product Type, without rewriting evidence."""
# Keep one SQL expression for counts, filtering and presentation. Ignore malformed
# values and select placeholders; never infer a taxonomy from product titles.
_VALUE = "trim(json_extract(data_json,'$.fields.dc_productType'), char(9)||char(10)||char(13)||' ')"
CATEGORY_SQL = f"""CASE
    WHEN json_extract(data_json,'$.source')='GigaB2B'
      AND json_type(data_json,'$.fields."GIGA category"')='text'
    THEN trim(json_extract(data_json,'$.fields."GIGA category"'))
    WHEN json_type(data_json,'$.fields.dc_productType')='text'
      AND lower({_VALUE}) NOT IN ('', '请选择', '请选择类目', 'please select', 'select', '--')
    THEN {_VALUE} ELSE '' END"""


def category_option(category):
    # Prefix avoids collisions between a real name and the missing-category key.
    return {'value': 'type:' + category if category else 'missing',
            'label': category or '未记录类目'}
