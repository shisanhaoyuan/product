"""Validate the complete, server-fetched MP_ITEM schema, including conditionals."""
import copy
import hashlib
import hmac
import html
import ipaddress
import re
from decimal import Decimal
from urllib.parse import urlparse

from jsonschema import Draft7Validator, FormatChecker, validators, ValidationError
from referencing import Registry
from referencing.exceptions import NoSuchResource

from .credential_vault import read_key
from .prelisting import fingerprint
from .walmart_client import WalmartError

# Official recommended MP_ITEM version, checked 2026-11-16. No guessed fallback.
DEFAULT_VERSION = '5.0.20260829-12_49_39-api'
LABELS = {
    'productName': '商品标题', 'brand': '品牌', 'shortDescription': '商品描述',
    'keyFeatures': '商品卖点', 'mainImageUrl': '主图', 'productSecondaryImageURL': '附图',
    'ShippingWeight': '运输重量（磅）', 'price': '售价', 'msrp': '建议零售价',
    'sku': '店铺 SKU', 'identification':'商品编码', 'productIdentifiers': '商品编码', 'productIdType': '编码类型', 'productId': '商品编码',
    'isProp65WarningRequired': '是否需要加州 65 号警告', 'has_written_warranty': '是否提供书面保修',
    'assembledProductLength': '组装后长度', 'assembledProductDepth': '组装后深度',
    'assembledProductHeight': '组装后高度', 'assembledProductWidth': '组装后宽度',
    'assembledProductWeight': '组装后重量', 'netContent': '净含量',
    'productNetContentMeasure': '数值', 'productNetContentUnit': '单位', 'measure': '数值', 'unit': '单位',
    'country_of_origin_substantial_transformation': '原产国（实质性转变）',
    'countryOfOriginSubstantialTransformation': '原产国（实质性转变）',
    'manufacturerPartNumber': '制造商零件编号', 'smallPartsWarningCode': '小零件警告',
    'color': '颜色', 'size': '尺寸', 'material': '材质', 'condition': '商品状况',
    'countPerPack': '每包数量', 'multipackQuantity': '组合包装数量', 'MustShipAlone': '是否必须单独运输',
    'fulfillmentLagTime': '备货天数', 'specProductType': '沃尔玛商品类型',
    'ageGroup':'适用年龄', 'colorCategory':'颜色分类','isAssemblyRequired':'是否需要组装',
    'seat_depth':'座深','smallPartsWarnings':'小零件警告','sofa_and_loveseat_design':'沙发款式',
    'upholstered':'是否软包','condition_notes':'成色说明','fulfillmentCenterID':'发货仓',
    'assemblyInstructions':'组装说明','suggested_number_of_people_for_assembly':'建议组装人数',
    'prop65WarningText':'加州 65 号警告内容','longDescription':'详细描述',
    'country_of_origin_textiles':'纺织品产地','countryOfOriginTextiles':'纺织品产地',
    'stateChemicalDisclosure':'州化学品披露','isFragile':'是否易碎',
    'hasNRTLListingCertification':'是否有 NRTL 认证','adjustableHeight':'是否可调高度',
    'seat_back_cushion_style':'靠背软垫款式','bedSize':'床尺寸',
    'recommendedLocations':'建议使用场所',
}


def plain(value):
    return html.unescape(re.sub('<[^>]*>', '', str(value or ''))).replace('\xa0', ' ').strip()


def taxonomy_options(raw):
    result = {}
    entries = raw.get('itemTaxonomy', [])
    if isinstance(entries, dict):
        entries = [entries]
    for category in entries:
        if not isinstance(category, dict):
            continue
        for group in category.get('productTypeGroup', []):
            for pt in group.get('productType', []):
                name = pt.get('productTypeName')
                if isinstance(name, str) and name.strip():
                    result[name] = dict(name=name, category=category.get('category', ''),
                                        group=group.get('productTypeGroupName', ''), description=pt.get('description', ''))
    if not result:
        raise WalmartError('沃尔玛未返回可用的 5.0 商品类型，请稍后重新获取')
    return sorted(result.values(), key=lambda x: (x['category'], x['group'], x['name']))


def normalize_spec(raw, product_type, version):
    schema = raw.get('schema', raw)
    try:
        assert schema.get('$schema', '').endswith(('draft-07/schema#', 'draft-07/schema'))
        props = schema['properties']
        item = props['MPItem']['items']['properties']
        assert product_type in item['Visible']['properties']
        assert version in props['MPItemFeedHeader']['properties']['version']['enum']
        assert 'sku' in item['Orderable']['properties']
        assert 'productIdentifiers' in item['Orderable']['properties']
    except (KeyError, TypeError, AttributeError, AssertionError):
        raise WalmartError('沃尔玛规格格式、类目或版本不匹配，已阻止使用；请重新获取官方规格') from None
    # No remote refs are followed by our validator (prevents arbitrary outbound requests).
    return schema


def sections(schema, product_type):
    item = schema['properties']['MPItem']['items']['properties']
    return dict(visible=item['Visible']['properties'][product_type], orderable=item['Orderable'],
                **{k: v for k, v in item.items() if k not in ('Visible', 'Orderable')})


def image_signature(digest):
    return hmac.new(read_key(), ('walmart-listing-image:v1:' + digest).encode(), hashlib.sha256).hexdigest()


def public_image(value, origin):
    if not isinstance(value, str):
        raise ValueError('图片地址格式不正确')
    local = re.fullmatch(r'/api/products/images/assets/([a-f0-9]{64})', value)
    if local:
        ensure_public_url(origin)
        digest = local[1]
        return origin.rstrip('/') + '/api/listing-media/' + digest + '/' + image_signature(digest)
    ensure_public_url(value)
    return value


def ensure_public_url(value):
    parsed = urlparse(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('图片必须使用公网 HTTPS 地址；本地图片需配置服务器公网 HTTPS 地址')
    hostname = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if (address is not None and not address.is_global) or hostname == 'localhost' or hostname.endswith(('.localhost','.local','.internal')):
        raise ValueError('图片不能使用本机或内网地址')
    if parsed.query and ('signature=' in parsed.query.lower() or 'x-oss-expires=' in parsed.query.lower()):
        raise ValueError('图片地址带有短期访问签名，请改用稳定的公网图片地址')


def identifier_errors(identity):
    if identity['mode'] == 'exemption':
        return [] if identity['submission_id'].strip() else ['请填写沃尔玛已批准的 GTIN 豁免申请编号']
    value, kind = identity['value'], identity['type']
    length = {'UPC': (12,), 'GTIN': (14,), 'EAN': (13,), 'ISBN': (10, 13)}[kind]
    if len(value) not in length or not value.isascii() or not (value.isdigit() or kind == 'ISBN' and len(value) == 10 and value[:-1].isdigit() and value[-1] == 'X'):
        return [f'{kind} 编码长度或格式错误，请填写真实编码并保留前导零']
    if len(value) == 10:
        valid = sum((10-i)*(10 if c == 'X' else int(c)) for i,c in enumerate(value)) % 11 == 0
    else:
        digits = list(map(int, value))
        valid = (sum(n * (3 if i % 2 == 0 else 1) for i,n in enumerate(reversed(digits[:-1]))) + digits[-1]) % 10 == 0
    return [] if valid else [f'{kind} 校验位不正确，请核对商品真实编码']


def feed_item(row, schema, version, origin, *, exemption=False, stock=None):
    visible = copy.deepcopy(row['visible'])
    for key in ('mainImageUrl',):
        if key in visible:
            visible[key] = public_image(visible[key], origin)
    if 'productSecondaryImageURL' in visible:
        visible['productSecondaryImageURL'] = [public_image(v, origin) for v in visible['productSecondaryImageURL']]
    orderable = copy.deepcopy(row['orderable'])
    orderable['sku'] = row['platform_sku']
    identity = row['identification']
    if identity['mode'] == 'exemption':
        # The approved exemption permits omitting identifiers. Never invent a GTIN.
        orderable.pop('productIdentifiers', None)
    else:
        orderable['productIdentifiers'] = {'productIdType': identity['type'], 'productId': identity['value']}
    supported = sections(schema, row['product_type'])['orderable']['properties']
    if 'inventory' in supported and stock is not None:
        orderable['inventory'] = [{'quantity':stock,'fulfillmentCenterID':row['fulfillment_center_id']}]
    if 'specProductType' in supported:
        orderable['specProductType'] = row['product_type']
    result = dict(Orderable=orderable, Visible={row['product_type']: visible}, **copy.deepcopy(row['extra']))
    if exemption:
        # Only this one requirement is relaxed AFTER the exact store/PT/brand approval was checked.
        schema = copy.deepcopy(schema)
        requirements = schema['properties']['MPItem']['items']['properties']['Orderable'].get('required', [])
        if 'productIdentifiers' in requirements:
            requirements.remove('productIdentifiers')
    return result, schema


def chinese_schema_error(error):
    path = '.'.join(map(str, error.absolute_path))
    name = LABELS.get(str(error.path[-1]), str(error.path[-1])) if error.path else '商品资料'
    if error.validator == 'required':
        missing = [v for v in error.validator_value if isinstance(error.instance, dict) and v not in error.instance]
        # jsonschema emits one error per missing property, even though validator_value
        # contains the complete required list. Preserve the exact field for navigation.
        field = next((v for v in missing if error.message == f'{v!r} is a required property'), missing[0] if missing else '')
        message = '缺少必填项：' + LABELS.get(field, field)
        if field:
            path += '.' + field
    elif error.validator == 'enum':
        message = name + '请选择沃尔玛允许的选项'
    elif error.validator in ('oneOf', 'anyOf', 'if', 'not', 'dependencies'):
        message = name + '不符合该类目的条件要求，请检查相关属性'
    elif error.validator in ('minLength', 'minItems'):
        message = f'{name}内容不足，至少需要 {error.validator_value} ' + ('条' if error.validator == 'minItems' else '个字符')
    elif error.validator in ('maxLength', 'maxItems'):
        message = f'{name}超出上限 {error.validator_value}'
    elif error.validator == 'additionalProperties':
        message = name + '包含当前类目不支持的属性，请移除或更换正确类目'
    elif error.validator == 'type':
        message = name + '格式不正确，应为 ' + str(error.validator_value)
    else:
        message = name + '不符合沃尔玛规则（' + error.validator + '）'
    return dict(path=path, message=message)


def precise_validator(schema, **kwargs):
    """Keep decimal multipleOf checks when nested official schemas repeat $schema.

    jsonschema.evolve otherwise switches our extended validator back to Draft7.
    Removing redundant declarations of the same dialect preserves its semantics.
    """
    def prepare(value):
        if isinstance(value, dict):
            return {k: v if k in ('enum','const','default','examples') else prepare(v) for k, v in value.items()
                    if not (k == '$schema' and isinstance(v, str) and
                            v.endswith(('draft-07/schema#', 'draft-07/schema')))}
        if isinstance(value, list):
            return [prepare(v) for v in value]
        return value

    def exact_multiple(validator, step, instance, definition):
        if isinstance(instance, (int, float, Decimal)) and not isinstance(instance, bool):
            if Decimal(str(instance)) % Decimal(str(step)) != 0:
                yield ValidationError('金额或数值精度不符合要求')

    cls = validators.extend(Draft7Validator, {'multipleOf': exact_multiple})
    return cls(prepare(schema), **kwargs)


def schema_errors(schema, item, version):
    def refuse(uri):
        raise NoSuchResource(ref=uri)
    payload = dict(MPItemFeedHeader={'businessUnit': 'WALMART_US', 'locale': 'en', 'version': version}, MPItem=[item])
    try:
        validator = precise_validator(schema, registry=Registry(retrieve=refuse), format_checker=FormatChecker())
        errors = [chinese_schema_error(e) for e in validator.iter_errors(payload)]
    except Exception:
        return [dict(path='', message='官方规格含无法解析的约束，不能确认资料完整；请重新获取规格并联系维护人员')]
    for key in ('productName', 'shortDescription'):
        visible = next(iter(item['Visible'].values()))
        if not plain(visible.get(key)):
            errors.append(dict(path='MPItem.0.Visible.' + next(iter(item['Visible'])) + '.' + key, message=LABELS[key] + '不能为空'))
    visible = next(iter(item['Visible'].values()))
    for index, feature in enumerate(visible.get('keyFeatures', [])):
        if not plain(feature):
            errors.append(dict(path=f'MPItem.0.Visible.{next(iter(item["Visible"]))}.keyFeatures.{index}',message=f'第 {index+1} 条卖点不能为空或仅包含空白 HTML'))
    return errors[:200]


def primary_image_error(text):
    return bool(re.search(r'\b(?:main\s*image(?:\s*url)?|primary\s+image)\b', text, re.I))


def upstream_reason(errors):
    """Provide concise Chinese categories while retaining the original codes/text."""
    result = []
    for error in errors if isinstance(errors, list) else []:
        if not isinstance(error, dict):
            continue
        code, original = str(error.get('code', ''))[:150], str(error.get('description', ''))[:1500]
        text = (code + ' ' + original).lower()
        if any(s in text for s in ('gtin', 'upc', 'product id', 'productidentifier')):
            message = '商品编码或豁免未通过，请核对 GTIN／UPC 及品牌、类目'
        elif primary_image_error(text):
            message = '沃尔玛未取得可用主图，请检查主图链接的公网访问及图片要求后重新提交'
        elif any(s in text for s in ('image', 'picture')):
            message = '商品图片未通过，请检查公网访问、格式及图片要求'
        elif any(s in text for s in ('required', 'missing', 'mandatory')):
            fields = [label for key,label in LABELS.items() if re.search(r'(?<![a-z0-9_])'+re.escape(key.lower())+r'(?![a-z0-9_])', original.lower())]
            message = '缺少必填资料：'+'、'.join(dict.fromkeys(fields)) if fields else '缺少沃尔玛必填资料，请按原始提示补齐属性'
        elif 'price' in text:
            message = '价格未通过，请核对售价和平台价格政策'
        elif any(s in text for s in ('category', 'product type', 'producttype')):
            message = '商品类目或属性不匹配，请检查所选类目'
        elif any(s in text for s in ('duplicate', 'already exist', 'sku')):
            message = '商品或 SKU 存在冲突，请核对该店铺已有商品'
        elif any(s in text for s in ('compliance', 'prohibited', 'restricted', 'policy')):
            message = '商品正在审核或受到平台限制，请检查合规要求'
        elif any(s in text for s in ('system', 'timeout', 'internal')):
            message = '沃尔玛系统处理异常，确认失败后可重试'
        else:
            message = '沃尔玛未通过此商品，请展开原始原因并修正'
        result.append(dict(code=code, message=message, original=original))
    return result
