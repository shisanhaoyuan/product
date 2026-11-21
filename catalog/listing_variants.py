"""Store-scoped Walmart variant families, derived only from source parent identity."""
import copy
import hashlib
import itertools
import json
import re
from decimal import Decimal

from referencing import Registry
from referencing.exceptions import NoSuchResource

from .walmart_listing_schema import LABELS, sections, precise_validator

POLICY = 'source_parent_variants_skill_v2'
SYSTEM_FIELDS = ('variantGroupId', 'variantAttributeNames', 'isPrimaryVariant')
EDITABLE = {'unsubmitted', 'failed', 'rejected', 'unpublished'}
ALIASES = {
    'color': ('color', 'color_name', 'colour', '颜色'),
    'size': ('size', 'size_name', '尺寸'),
    'sizeDescriptor': ('sizeDescriptor', 'size', 'size_name', '尺寸'),
    'bedSize': ('bedSize', 'bed size', '床尺寸'),
    'multipackQuantity': ('multipackQuantity', 'multipack quantity', 'number of items', 'item package quantity', '多件装数量'),
    'countPerPack': ('countPerPack', 'count per pack', '每包数量'),
    'pattern': ('pattern', '图案'),
    'material': ('material', '材质'),
    'style': ('style', '款式'),
}


def group_id(store_id, listing_id):
    return 'JW-' + hashlib.sha256(f'{store_id}:{listing_id}'.encode()).hexdigest()[:32]


def token(value):
    return re.sub(r'[\W_]+', '', str(value).casefold())


def value_key(value):
    if isinstance(value, str):
        return ' '.join(value.split()).casefold()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return format(Decimal(str(value)).normalize(), 'f')
    if isinstance(value, list):
        return json.dumps(sorted(value_key(v) for v in value), ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps({k: value_key(v) for k, v in value.items()}, sort_keys=True, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def valid_attribute(shape, value):
    def refuse(uri):
        raise NoSuchResource(ref=uri)
    try:
        return precise_validator(shape, registry=Registry(retrieve=refuse)).is_valid(value)
    except Exception:
        # A fragment with unresolved refs cannot establish a valid discriminator.
        return False


def allowed_axes(schema, product_type):
    if not schema:
        return [], {}
    props = sections(schema, product_type)['visible'].get('properties', {})
    names = props.get('variantAttributeNames', {}).get('items', {}).get('enum', [])
    return [k for k in names if isinstance(k, str) and k in props and k not in SYSTEM_FIELDS], props


def source_value(content, axis, shape):
    attrs = content.get('attributes') or {}
    if not isinstance(attrs, dict):
        return None
    names = {token(k) for k in ALIASES.get(axis, (axis,))}
    values = [v for k, v in attrs.items() if token(k) in names and v not in (None, '', [], {})]
    if len({value_key(v) for v in values}) != 1:
        return None
    value = values[0]
    if isinstance(value, str):
        value = value.strip()
        if shape.get('type') == 'integer' and re.fullmatch(r'\d+', value):
            value = int(value)
        elif shape.get('type') == 'number':
            try:
                value = float(value)
            except ValueError:
                pass
        elif shape.get('type') == 'array':
            value = [value]
    return value if valid_attribute(shape, value) else None


def plan(rows, basis, schemas, *, sources=None, history=None, latest=None, apply=False):
    """Pure planner. Apply only to editable rows; validate locked/history members too.

    History is latest attempted row per store SKU, scoped to these source parents.
    No invented axis values or grouping by similar titles/ASIN prefixes.
    """
    sources, history, latest = sources or {}, history or {}, latest or {}
    original = rows
    rows = copy.deepcopy(rows)
    groups = []
    for item in basis['items']:
        lid = item['listing_id']
        current = [r for r in rows if r['listing_id'] == lid]
        if not current:
            continue
        old = {sku: h for sku, h in history.items() if h['row']['listing_id'] == lid}
        current_skus = {r['platform_sku'] for r in current}
        others = [h['row'] for sku, h in old.items() if sku not in current_skus and h['status'] not in {'failed', 'rejected'}]
        members = current + others
        source_count = len(sources.get(lid, {}).get('variants') or item['variants'])
        multi = max(len(members), source_count) > 1
        if not multi and not any(r['visible'].get(k) for r in members for k in SYSTEM_FIELDS):
            continue
        errors = []
        def issue(message, path='visible.variantAttributeNames'):
            errors.append(dict(path=path, message=message, code='variant_group'))
        pt = current[0]['product_type']
        allowed, props = allowed_axes(schemas.get(pt), pt)
        gid = group_id(basis['store']['id'], lid)
        if not multi:
            issue('单规格商品不能手动关联其他商品的变体组，请清除变体组字段')
        if len(members) < 2:
            issue('该来源商品包含多个变体，首次组建变体组请在同一批次选择至少两个子 SKU')
        if len({r['product_type'] for r in members}) != 1:
            issue('同组子 SKU 的沃尔玛商品类型不一致，请统一真实类目', 'product_type')
        if len({r['visible'].get('brand', '') for r in members}) != 1:
            issue('同组子 SKU 的品牌不一致，请核对店铺品牌', 'visible.brand')
        if not all(k in props for k in SYSTEM_FIELDS) or not allowed:
            issue('当前类目未提供可用的变体属性，请获取官方字段或核对商品类型')
        historical = [h['row'] for h in old.values() if h['row']['visible'].get('variantGroupId')]
        legacy = [h for h in old.values() if not h['row']['visible'].get('variantGroupId') and h['status'] not in {'failed', 'rejected'}]
        if legacy:
            issue('该商品已有按单品提交的记录，需先核对沃尔玛现有商品关系；本次不会自动重复上架或迁移旧商品')
        fixed_ids = {r['visible']['variantGroupId'] for r in historical}
        if fixed_ids and fixed_ids != {gid}:
            issue('历史变体组编号与当前店铺／来源商品不符，请核对历史记录', 'visible.variantGroupId')
        prior_axes = [r['visible'].get('variantAttributeNames', []) for r in historical]
        choices = [r['visible'].get('variantAttributeNames') for r in current if r['visible'].get('variantAttributeNames')]
        axes = (prior_axes or choices or [[]])[0]
        if not isinstance(axes, list) or not all(isinstance(k, str) for k in axes):
            axes = []
            issue('变体属性格式不正确，请重新选择')
        if prior_axes and any(v != axes for v in prior_axes + choices):
            issue('已提交变体组的差异属性不能更换，请保留原分组属性')
        elif choices and any(v != axes for v in choices):
            issue('同组子 SKU 选择的差异属性不一致，请统一')
        content = {v['platform_sku']: v.get('content') or {} for v in item['variants']}
        for row in current:
            if not apply or latest.get(row['platform_sku'], {}).get('status', 'unsubmitted') not in EDITABLE:
                continue
            for key in allowed:
                if row['visible'].get(key) in (None, '', [], {}):
                    value = source_value(content.get(row['platform_sku'], {}), key, props[key])
                    if value is not None:
                        row['visible'][key] = value
        def valid_value(row, key):
            value = row['visible'].get(key)
            return value not in (None, '', [], {}) and valid_attribute(props[key], value)
        def combinations(keys):
            return [tuple(value_key(r['visible'].get(k)) for k in keys) for r in members]
        def choose_axes(candidate_rows, candidates_allowed):
            candidates = [k for k in candidates_allowed if all(valid_value(r, k) for r in candidate_rows)
                          and len({value_key(r['visible'][k]) for r in candidate_rows}) > 1]
            def combinations(keys):
                return [tuple(value_key(r['visible'].get(k)) for k in keys) for r in candidate_rows]
            selected = []
            # Bound search by the actual spec's attribute limit; never manufacture a discriminator.
            limit = min(len(candidates), props['variantAttributeNames'].get('maxItems', len(candidates)))
            if len(set(combinations(candidates))) == len(candidate_rows):
                choices = itertools.chain.from_iterable(itertools.combinations(candidates, size)
                                                        for size in range(1, limit + 1))
                selected = next((list(keys) for keys in itertools.islice(choices, 2048)
                             if len(set(combinations(keys))) == len(candidate_rows)), [])
                if not selected:
                    # Avoid exponential work for unusually large official enums.
                    reduced = list(candidates)
                    for key in reversed(candidates):
                        remaining = [k for k in reduced if k != key]
                        if len(set(combinations(remaining))) == len(candidate_rows):
                            reduced = remaining
                    if len(reduced) <= limit:
                        selected = reduced
            return selected
        from .listing_variant_rules import mapped_source, name as rule_name, FORBIDDEN
        # Existing submitted families retain their original routing. New families
        # use the user's latest Skill rule: Multipack Quantity is not a group axis.
        if not historical:
            allowed = [k for k in allowed if rule_name(k) not in FORBIDDEN]
        proposals, mappings = mapped_source(current, content, allowed, props)
        axes_valid = (bool(axes) and len(set(axes)) == len(axes) and all(k in allowed for k in axes)
                      and all(valid_value(r,k) for r in members for k in axes)
                      and len(set(combinations(axes))) == len(members))
        if apply and not historical and not legacy and not errors and not axes_valid and len(members) > 1 and all(
                latest.get(r['platform_sku'],{}).get('status','unsubmitted') in EDITABLE for r in current):
            mapped_keys = list(dict.fromkeys(m['target'] for m in mappings))
            replacement_axes = choose_axes(proposals, mapped_keys)
            if replacement_axes:
                for row, proposed in zip(current,proposals):
                    for key in replacement_axes:
                        row['visible'][key] = proposed['visible'][key]
                axes = replacement_axes
        if not axes and allowed:
            axes = choose_axes(members, allowed)
        # Show only provenance for values actually retained, never a proposal
        # that failed official type/enum/length checks or a later manual edit.
        applied_targets = {k for k in axes if all(r['visible'].get(k) == p['visible'].get(k)
                           for r,p in zip(current,proposals)) and any(m['target']==k for m in mappings)}
        rule_mappings = [m for m in mappings if m['target'] in applied_targets]
        if not axes:
            issue('无法用当前类目允许的属性区分子 SKU；请补齐真实颜色、尺寸或包装数量等差异，不能直接作为独立商品提交')
        elif any(k not in allowed for k in axes) or len(set(axes)) != len(axes):
            issue('所选差异属性不在该类目允许范围内，或重复选择')
        else:
            for row in members:
                for key in axes:
                    if not valid_value(row, key):
                        issue(f"{row['source_sku']}：请填写有效的{LABELS.get(key, key)}以区分变体", 'visible.' + key)
            combos = combinations(axes)
            if len(set(combos)) != len(combos):
                issue('同组子 SKU 的差异属性组合重复，请核对真实规格；系统将按 Skill 匹配来源规格；请核对是否保留了原始颜色、尺寸等差异')
        previous_primary = {r['platform_sku'] for r in historical if r['visible'].get('isPrimaryVariant') == 'Yes'}
        chosen_primary = [r['platform_sku'] for r in members if r['visible'].get('isPrimaryVariant') == 'Yes']
        primary = next(iter(previous_primary)) if len(previous_primary) == 1 else (chosen_primary[0] if len(chosen_primary) == 1 else min(current_skus))
        if len(previous_primary) > 1 or len(chosen_primary) > 1:
            issue('同组只能有一个主变体，请重新选择', 'visible.isPrimaryVariant')
        if previous_primary and any(s != primary for s in chosen_primary):
            issue('已提交变体组的主变体不能更换', 'visible.isPrimaryVariant')
        if not any(r['platform_sku'] == primary for r in members):
            issue('原主变体尚未成功，须将原主变体加入本批次重试', 'visible.isPrimaryVariant')
        for row in current:
            expected = dict(variantGroupId=gid, variantAttributeNames=axes, isPrimaryVariant='Yes' if row['platform_sku'] == primary else 'No')
            editable = latest.get(row['platform_sku'], {}).get('status', 'unsubmitted') in EDITABLE
            if apply and multi and editable and not errors:
                row['visible'].update(expected)
                # A real image of this SKU is a valid visual swatch; never copy a sibling's image.
                if 'color' in axes and 'swatchImages' in props and not row['visible'].get('swatchImages'):
                    url = content.get(row['platform_sku'], {}).get('swatch_image') or row['visible'].get('mainImageUrl')
                    if url:
                        row['visible']['swatchImages'] = [dict(swatchVariantAttribute='color', swatchImageUrl=url)]
            elif not errors and any(row['visible'].get(k) != v for k, v in expected.items()):
                issue(f"{row['source_sku']}：变体组字段不完整或不一致，请保存后重新校验", 'visible.variantGroupId')
        groups.append(dict(listing_id=lid, group_id=gid, axes=axes, allowed_axes=allowed, primary_sku=primary,
                           skus=[r['platform_sku'] for r in current],
                           members=[dict(platform_sku=r['platform_sku'], source_sku=r['source_sku'],
                                         values={k:r['visible'].get(k) for k in axes},
                                         primary=r['platform_sku']==primary, in_batch=r['platform_sku'] in current_skus)
                                    for r in members], errors=errors, rule_mappings=rule_mappings, locked=bool(historical or legacy)))
    if apply:
        original[:] = rows
    return groups


def context(db, rows, basis, version):
    from .walmart_publishing import cache_value
    bound = db.execute('SELECT * FROM walmart_accounts WHERE store_id=?', (basis['store']['id'],)).fetchone()
    schemas = {r['product_type']: cache_value(db, bound, 'spec', version, r['product_type']) if bound else None for r in rows}
    sources, history = {}, {}
    for item in basis['items']:
        lid = item['listing_id']
        source = db.execute('SELECT data_json FROM current_listings WHERE id=?', (lid,)).fetchone()
        sources[lid] = json.loads(source[0]) if source else {}
        records = db.execute('''SELECT a.rows_json,a.results_json FROM walmart_listing_attempts a
            JOIN prelisting_items p ON p.batch_id=a.batch_id
            WHERE a.store_id=? AND a.partner_id=? AND p.listing_id=? ORDER BY a.created_at,a.id''',
            (basis['store']['id'], bound['partner_id'] if bound else '', lid))
        for record in records:
            results = json.loads(record['results_json'])
            for row in json.loads(record['rows_json']):
                if row['listing_id'] == lid:
                    history[row['platform_sku']] = dict(row=row, status=results.get(row['platform_sku'], {}).get('status', 'unknown'))
    return dict(schemas=schemas, sources=sources, history=history)


def synchronize(db, rows, basis, version, latest, *, apply=False):
    return plan(rows, basis, latest=latest, apply=apply, **context(db, rows, basis, version))


def submission_errors(groups, selected, latest, history):
    """Require all editable batch siblings; allow retries beside successful siblings."""
    errors = []
    for group in groups:
        if not set(group['skus']) & set(selected):
            continue
        errors.extend(e['message'] for e in group['errors'])
        for member in group['members']:
            sku = member['platform_sku']
            status = latest.get(sku, history.get(sku, {})).get('status', 'unsubmitted')
            if member['in_batch'] and status in EDITABLE and sku not in selected:
                errors.append('同一变体组尚未提交或失败的子 SKU 必须一起提交，请选中整组')
            elif status in {'processing', 'unknown'}:
                errors.append('同组仍有处理中的商品或结果未知，请查询原批次结果后再重试')
    return list(dict.fromkeys(errors))
