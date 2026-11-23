"""Remove only fields explicitly rejected by the selected official schema."""
import copy
import re

from referencing import Registry
from referencing.exceptions import NoSuchResource

from .walmart_listing_schema import precise_validator, sections

# Routing, identity and commerce failures need an explicit correction, never
# silent removal that might submit a different product or a standalone variant.
PROTECTED = {'sku', 'productIdentifiers', 'price', 'msrp', 'inventory',
             'SkuUpdate', 'ProductIdUpdate', 'fulfillmentCenterID',
             'variantGroupId', 'variantAttributeNames', 'isPrimaryVariant'}


def additional_fields(instance, definition):
    if not isinstance(instance, dict) or definition.get('additionalProperties') is not False:
        return []
    properties = definition.get('properties', {})
    patterns = definition.get('patternProperties', {})
    return sorted(key for key in instance if key not in properties and
                  not any(re.search(pattern, key) for pattern in patterns))


def cleanup(row, schema):
    audit = {}
    def refuse(uri):
        raise NoSuchResource(ref=uri)
    for section, definition in sections(schema, row['product_type']).items():
        if section not in ('visible', 'orderable'):
            continue
        try:
            errors = list(precise_validator(definition, registry=Registry(retrieve=refuse)).iter_errors(row[section]))
        except Exception:
            # Unresolvable constraints do not authorize changing saved content.
            continue
        for error in errors:
            if error.validator != 'additionalProperties':
                continue
            for key in additional_fields(error.instance, error.schema):
                path = [*error.absolute_path, key]
                if len(path) == 1 and key in PROTECTED:
                    continue
                target = row[section]
                for part in path[:-1]:
                    target = target[part]
                if key in target:
                    previous = target.pop(key)
                    audit['.'.join(map(str, [section, *path]))] = dict(
                        kind='schema_cleanup', value=None, previous_value=copy.deepcopy(previous), refs=[],
                        reason=f'当前 {row["product_type"]} 类目不接受此旧属性，已移出提交资料，原值保留在审计中')
    return audit
