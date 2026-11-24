"""Adapt official Canadian MP_ITEM_INTL JSON schemas to the shared editor.

Canada's online Get spec/Taxonomy endpoints are not available in Global APIs.
The official downloadable schema is bundled so first use needs no network or
manual upload. An administrator may import a compatible replacement schema.
The original schema remains authoritative at final validation; it is never
replaced with US rules or an inferred list of required fields.
"""
import copy
import gzip
import hashlib
import json
import re
from functools import lru_cache
from importlib.resources import files

from jsonschema.validators import validator_for

from .walmart_client import WalmartError

DEFAULT_NODE = '__DEFAULT_CA__'
CONTENT = {'productName', 'brand', 'shortDescription', 'longDescription',
           'keyFeatures', 'mainImageUrl', 'productSecondaryImageURL'}
OFFICIAL_URL = 'https://developer.walmart.com/file/mp/ca/CA_MP_ITEM_INTL_SPEC.json'


def clean_annotations(value):
    """The official file has string `examples`; Draft 7 expects an array.

    These are documentation annotations, not assertions. Retain every validation
    constraint, including required/enum/oneOf and numeric precision.
    """
    if not isinstance(value, dict):
        return value
    result = copy.deepcopy(value)
    if 'examples' in result and not isinstance(result['examples'], list):
        result.pop('examples')
    # Walk schema locations only: a property actually named examples, or a
    # literal enum/default object, must never lose its assertion or data.
    for key in ('properties', 'patternProperties', 'definitions', '$defs'):
        if isinstance(result.get(key), dict):
            result[key] = {name: clean_annotations(shape) for name, shape in result[key].items()}
    for key in ('items', 'additionalItems', 'additionalProperties', 'contains',
                'propertyNames', 'not', 'if', 'then', 'else'):
        shape = result.get(key)
        if isinstance(shape, list):
            result[key] = [clean_annotations(child) for child in shape]
        elif isinstance(shape, dict):
            result[key] = clean_annotations(shape)
    for key in ('allOf', 'anyOf', 'oneOf'):
        if isinstance(result.get(key), list):
            result[key] = [clean_annotations(child) for child in result[key]]
    if isinstance(result.get('dependencies'), dict):
        result['dependencies'] = {key: clean_annotations(shape) if isinstance(shape,dict) else shape
                                  for key,shape in result['dependencies'].items()}
    return result


def bundle_digest(bundle):
    return hashlib.sha256(json.dumps({'adapter': 1, 'version': bundle['version'],
        'specs': bundle['specs']}, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def single_type_schema(original, product_type):
    """Copy only the selected branch rather than all 80 types for every type."""
    props = original['properties']
    item = props['MPItem']['items']
    item_props = item['properties']
    visible = item_props['Visible']
    return copy.deepcopy({**original, 'properties': {**props, 'MPItem': {
        **props['MPItem'], 'items': {**item, 'properties': {**item_props, 'Visible': {
            **visible, 'properties': {product_type: visible['properties'][product_type]}}}}}}})


@lru_cache(maxsize=1)
def official_bundle():
    """Read the verified, immutable official snapshot once per worker process."""
    try:
        root = files('catalog').joinpath('specifications')
        manifest = json.loads(root.joinpath('walmart-ca-mp-item-intl.json').read_text())
        with root.joinpath(manifest['file']).open('rb') as saved, gzip.GzipFile(fileobj=saved) as stream:
            raw = stream.read(15 * 1024 * 1024 + 1)
        if len(raw) > 15 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != manifest['sha256']:
            raise ValueError()
        bundle = import_specs(json.loads(raw))
        if bundle['version'] != manifest['version'] or len(bundle['specs']) != manifest['product_types']:
            raise ValueError()
        bundle['source'] = {'kind': 'bundled_official', 'url': OFFICIAL_URL,
                            'sha256': manifest['sha256'], 'verified_at': manifest['verified_at']}
        return bundle
    except (OSError, EOFError, ValueError, KeyError, WalmartError):
        raise WalmartError('内置加拿大官方规格无法读取或完整性检查失败，请重新部署或导入完整官方规格') from None


def local_refs(schema):
    visited = 0
    def resolve(value, trail=()):
        nonlocal visited
        visited += 1
        if visited > 100000 or len(trail) > 40:
            raise WalmartError('加拿大规格结构过大，请按商品类型拆分后导入')
        if isinstance(value, list):
            return [resolve(v, trail) for v in value]
        if not isinstance(value, dict):
            return value
        if '$ref' in value:
            ref = value['$ref']
            if not isinstance(ref, str) or not ref.startswith('#/') or ref in trail:
                raise WalmartError('加拿大规格含外部或循环引用，请导入独立完整的官方 JSON 规格')
            target = schema
            for key in ref[2:].split('/'):
                target = target[key.replace('~1', '/').replace('~0', '~')]
            return resolve(target, (*trail, ref))
        return {k: resolve(v, trail) for k, v in value.items()}
    return resolve(schema)


def localized(shape):
    return shape.get('type') == 'object' and 'en' in shape.get('properties', {})


def flatten(shape):
    if isinstance(shape, list):
        return [flatten(v) for v in shape]
    if not isinstance(shape, dict):
        return shape
    if localized(shape):
        if any(k != 'en' for k in shape.get('required', [])):
            raise WalmartError('该加拿大规格要求其他语言必填，当前英文刊登适配暂不支持，请使用允许英文刊登的官方规格')
        return flatten(shape['properties']['en'])
    result = {k: flatten(v) for k, v in shape.items()}
    # Draft 4 uses boolean exclusivity; the shared editor uses Draft 7.
    # Canonical submission validation still uses the original dialect.
    for exclusive, inclusive in (('exclusiveMinimum','minimum'),('exclusiveMaximum','maximum')):
        if isinstance(result.get(exclusive), bool):
            enabled = result.pop(exclusive)
            if enabled and inclusive in result:
                result[exclusive] = result.pop(inclusive)
    return result


def encode(value, shape):
    if localized(shape):
        return {'en': encode(value, shape['properties']['en'])}
    if isinstance(value, dict):
        return {k: encode(v, shape.get('properties', {}).get(k, {})) for k, v in value.items()}
    if isinstance(value, list):
        return [encode(v, shape.get('items', {})) for v in value]
    return value


def import_specs(raw):
    """Accept a raw official schema or a bundle of {schema, header} entries."""
    try:
        if len(json.dumps(raw, allow_nan=False).encode()) > 15 * 1024 * 1024:
            raise ValueError()
        entries = raw.get('specifications', [raw])
        if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
            raise ValueError()
        specs, versions = {}, set()
        for entry in entries:
            original = copy.deepcopy(entry.get('schema', entry))
            if original.get('$schema') not in ('http://json-schema.org/draft-07/schema#', 'http://json-schema.org/draft-04/schema#',
                                               'https://json-schema.org/draft-07/schema', 'http://json-schema.org/draft-07/schema'):
                raise ValueError()
            original = clean_annotations(local_refs(original))
            validator_for(original).check_schema(original)
            props = original['properties']
            header_shape = props['MPItemFeedHeader']
            hprops = header_shape['properties']
            header = copy.deepcopy(entry.get('header', {}))
            for key, shape in hprops.items():
                if key not in header:
                    if 'const' in shape:
                        header[key] = shape['const']
                    elif len(shape.get('enum', [])) == 1:
                        header[key] = shape['enum'][0]
                    elif 'default' in shape:
                        header[key] = shape['default']
            locale = hprops.get('locale', {})
            if 'locale' not in header and locale.get('type') == 'array' and set(
                    locale.get('items', {}).get('enum', [])) == {'en', 'fr'}:
                # Official CA 3.16 permits both languages in the header, but its
                # localized content objects require only English.
                header['locale'] = ['en', 'fr']
            if header.get('mart') != 'WALMART_CA' or not isinstance(header.get('version'), str):
                raise WalmartError('请导入 Walmart 加拿大站官方规格；Feed Header 必须明确 WALMART_CA 和官方版本')
            if any(header.get(k,'WALMART_CA')!='WALMART_CA' for k in ('tenant','businessUnit')):
                raise WalmartError('加拿大 Header 不能包含其他站点的 tenant 或 businessUnit')
            # The consolidated CA 3.16 schema makes subCategory optional and
            # provides no PT->subCategory association. Omit it unless supplied
            # or fixed by a category-specific schema; never guess that mapping.
            if 'subCategory' in header and (not isinstance(header['subCategory'], str) or not header['subCategory']):
                raise WalmartError('加拿大 Header 的 subCategory 格式不正确')
            if header.get('feedType', 'MP_ITEM_INTL') != 'MP_ITEM_INTL':
                raise WalmartError('当前加拿大刊登使用 MP_ITEM_INTL，请导入对应官方规格，不能混用 MP_ITEM')
            if list(validator_for(original)(header_shape).iter_errors(header)):
                raise WalmartError('加拿大 Header 不符合导入的官方规格，请核对版本、语言和子类目')
            versions.add(header['version'])
            if len(header['version']) > 80 or not re.fullmatch(r'[0-9]+\.[A-Za-z0-9_.-]+',header['version']):
                raise ValueError()
            item = props['MPItem']['items']['properties']
            assert {'sku', 'productIdentifiers'} <= set(item['Orderable']['properties'])
            for product_type, visible in item['Visible']['properties'].items():
                if not isinstance(product_type, str) or not 1 <= len(product_type) <= 300 or product_type in specs:
                    raise ValueError()
                # Keep each PT's canonical schema small; sibling types do not
                # participate in validating this one-item payload.
                canonical = single_type_schema(original, product_type)
                internal = flatten(copy.deepcopy(canonical))
                internal['$schema'] = 'http://json-schema.org/draft-07/schema#'
                parts = internal['properties']['MPItem']['items']['properties']
                vp = parts['Visible']['properties'][product_type]
                moved = sorted(CONTENT & item['Orderable']['properties'].keys())
                for key in moved:
                    vp.setdefault('properties', {})[key] = parts['Orderable']['properties'].pop(key)
                    if key in parts['Orderable'].get('required', []):
                        parts['Orderable']['required'].remove(key)
                        vp.setdefault('required', []).append(key)
                internal['_ca'] = {'header': header, 'original': canonical, 'moved': moved, 'product_type': product_type}
                internal['properties']['MPItemFeedHeader']['properties']['version'] = {'enum': [header['version']]}
                # The imported schema supplies the complete conditional constraints.
                # This normalized view supplies editor fields; canonical validation is below.
                specs[product_type] = internal
        if len(versions) != 1 or not specs or len(specs) > 1000:
            raise ValueError()
        if len(json.dumps(specs).encode()) > 15 * 1024 * 1024:
            raise WalmartError('加拿大规格总量超过 15 MB，请按当前需要的商品类型导入')
        bundle = {'version': versions.pop(), 'specs': specs}
        bundle['digest'] = bundle_digest(bundle)
        return bundle
    except WalmartError:
        raise
    except Exception:
        # Import errors must not echo product contents or arbitrary schema text.
        raise WalmartError('无法读取加拿大官方 JSON 规格，请使用完整 JSON Schema，商品示例 JSON 不能代替类目规格') from None


def taxonomy(bundle):
    specs = (bundle or {}).get('specs', {})
    if not specs:
        raise WalmartError('请先在填写页面导入加拿大 MP_ITEM_INTL 官方 JSON 规格，不能调用美国类目接口代替')
    return {'itemTaxonomy': [{'category': s['_ca']['header'].get('subCategory', pt), 'productTypeGroup': [
        {'productTypeGroupName': '加拿大商品类型', 'productType': [{'productTypeName': pt}]}]}
        for pt, s in sorted(specs.items())]}


def convert_item(item, schema):
    meta = schema['_ca']
    canonical = copy.deepcopy(meta['original'])
    shapes = canonical['properties']['MPItem']['items']['properties']
    result = copy.deepcopy(item)
    visible = result['Visible'][meta['product_type']]
    for key in meta['moved']:
        if key in visible:
            result['Orderable'][key] = visible.pop(key)
    for section, values in result.items():
        result[section] = encode(values, shapes.get(section, {}))
    canonical['_ca'] = {k: v for k, v in meta.items() if k != 'original'}
    return result, canonical
