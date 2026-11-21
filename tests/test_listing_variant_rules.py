import copy
import json

from catalog.listing_variant_rules import mapped_source
from catalog.listing_variants import plan
from catalog.walmart_listing_schema import sections
from test_listing_variants import family, variant_publishing
from test_walmart_publishing import ready, PT, submission
from test_walmart_accounts import vault


def setup_rules(columns, fields):
    rows, basis, schemas = family()
    shape = sections(schemas[PT], PT)['visible']['properties']
    shape.update(fields)
    shape['variantAttributeNames']['items']['enum'] = list(fields)
    for row, attrs in zip(basis['items'][0]['variants'], columns):
        row['content']['attributes'] = attrs
    return rows, basis, schemas


def test_duplicate_count_repaired_from_raw_size_without_touching_business_fields():
    rows, basis, schemas = setup_rules([{'Size': '5-Tier (2 Pack)'}, {'Size': '5-Tier (1 Pack)'}],
                                      {'color': {'type': 'string', 'maxLength': 100}, 'count': {'type': 'integer'}})
    for n, row in enumerate(rows):
        row.update(orderable={'price': 50+n}, identification={'value': str(n)}, target_stock=10+n)
        row['visible'].update(count=1, color='Brown', shortDescription='Keep text', variantAttributeNames=['count'])
    before = copy.deepcopy(rows)
    group = plan(rows, basis, schemas, apply=True)[0]
    assert not group['errors'] and group['axes'] == ['color']
    assert [r['visible']['color'] for r in rows] == ['5-Tier (2 Pack)', '5-Tier (1 Pack)']
    assert group['rule_mappings'][0]['method'] == 'user_authorized_custom_fallback'
    assert group['rule_mappings'][0]['source'] == 'Size'
    for old, row in zip(before, rows):
        for field in ('orderable', 'identification', 'target_stock', 'listing_id', 'platform_sku', 'source_sku'):
            assert row[field] == old[field]
        for field in ('shortDescription', 'mainImageUrl', 'count'):
            assert row['visible'][field] == old['visible'][field]
    saved = copy.deepcopy(rows)
    assert not plan(rows, basis, schemas, apply=True)[0]['errors'] and rows == saved
    assert plan(rows, basis, schemas)[0]['rule_mappings'][0]['source_values']['SKU-0'] == '5-Tier (2 Pack)'


def test_all_exact_names_reserved_before_semantic_substitution():
    rows, basis, schemas = setup_rules([{'Colour': 'Brown', 'Size': '2 Pack'}, {'Colour': 'Black', 'Size': '1 Pack'}],
                                      {'color': {'type': 'string'}, 'finish': {'type': 'string'}})
    content = {v['platform_sku']: v['content'] for v in basis['items'][0]['variants']}
    props = sections(schemas[PT], PT)['visible']['properties']
    proposed, mappings = mapped_source(rows, content, ['finish', 'color'], props)
    assert {m['source']: m['target'] for m in mappings} == {'Colour': 'color', 'Size': 'finish'}
    assert proposed[0]['visible']['color'] == 'Brown' and proposed[0]['visible']['finish'] == '2 Pack'
    # Result does not depend on source column/menu/SKU ordering.
    repeated, _ = mapped_source(list(reversed(rows)), content, ['color', 'finish'], props)
    assert {r['platform_sku']: r['visible'] for r in repeated} == {r['platform_sku']: r['visible'] for r in proposed}


def test_overflow_merges_own_values_and_never_doubles_separator_or_loses_units():
    rows, basis, schemas = setup_rules([{'Color': 'Black', 'Size': '24 Inch', 'Weight': '55 lbs'},
                                      {'Color': 'Black', 'Size': '32 Inch', 'Weight': '65 lbs'}],
                                      {'color': {'type': 'string', 'maxLength': 100}})
    group = plan(rows, basis, schemas, apply=True)[0]
    assert not group['errors'] and group['axes'] == ['color']
    assert [r['visible']['color'] for r in rows] == ['Black**24 Inch**55 lbs', 'Black**32 Inch**65 lbs']
    assert {m['method'] for m in group['rule_mappings']} == {'exact', 'combined_into_existing'}
    snapshot = copy.deepcopy(rows)
    plan(rows, basis, schemas, apply=True)
    assert rows == snapshot


def test_restricted_api_fields_and_missing_or_identical_sources_never_fabricate_difference():
    for color_shape in ({'type': 'string', 'enum': ['Red', 'Blue']}, {'type': 'string', 'maxLength': 3}):
        rows, basis, schemas = setup_rules([{'Size': '24 Inch'}, {'Size': '32 Inch'}], {'color': color_shape})
        result = plan(rows, basis, schemas, apply=True)[0]
        assert result['errors'] and not result['axes']
        assert all('color' not in row['visible'] for row in rows)
    for values in ([{'Color': 'Brown'}, {'Color': 'Brown'}], [{'Size': '24 Inch'}, {}],
                   [{'ASIN': 'B000000001'}, {'ASIN': 'B000000002'}]):
        rows, basis, schemas = setup_rules(values, {'color': {'type': 'string'}})
        assert plan(rows, basis, schemas, apply=True)[0]['errors']


def test_multipack_not_selected_but_valid_independent_quantity_preserved():
    rows, basis, schemas = setup_rules([{'Multipack Quantity': '2 Pack'}, {'Multipack Quantity': '1 Pack'}],
                                      {'color': {'type': 'string'}, 'multipackQuantity': {'type': 'integer'}})
    for row in rows:
        row['visible']['multipackQuantity'] = 7
    group = plan(rows, basis, schemas, apply=True)[0]
    assert not group['errors'] and group['allowed_axes'] == ['color'] and group['axes'] == ['color']
    assert [r['visible']['color'] for r in rows] == ['2 Pack', '1 Pack']
    assert all(r['visible']['multipackQuantity'] == 7 for r in rows)


def test_manual_valid_group_and_submitted_family_are_not_overwritten():
    rows, basis, schemas = setup_rules([{'Size': '2 Pack'}, {'Size': '1 Pack'}], {'color': {'type': 'string'}})
    for n, row in enumerate(rows):
        row['visible'].update(color=['Manual Red', 'Manual Blue'][n], variantAttributeNames=['color'])
    group = plan(rows, basis, schemas, apply=True)[0]
    assert not group['errors'] and [r['visible']['color'] for r in rows] == ['Manual Red', 'Manual Blue']
    history = {r['platform_sku']: {'row': copy.deepcopy(r), 'status': 'success'} for r in rows}
    latest = {s: {'status': 'success'} for s in history}
    before = copy.deepcopy(rows)
    plan(rows, basis, schemas, history=history, latest=latest, apply=True)
    assert rows == before


def test_api_repairs_legacy_duplicate_selection_and_records_skill_provenance(variant_publishing):
    c, catalog, _, _, b, base, remote = variant_publishing
    doc = ready(variant_publishing)
    with catalog.connect() as db:
        stored = db.execute('SELECT basis_json FROM walmart_listing_documents WHERE batch_id=?', (b['id'],)).fetchone()
        basis = json.loads(stored[0])
        for n, variant in enumerate(basis['items'][0]['variants']):
            variant['content']['attributes'] = {'Size': ['5-Tier (2 Pack)', '5-Tier (1 Pack)'][n]}
        for row in doc['rows']:
            row['visible'].update(color='Brown', variantAttributeNames=['color'])
            for field in ('variantGroupId', 'isPrimaryVariant'):
                row['visible'].pop(field, None)
        db.execute('UPDATE walmart_listing_documents SET rows_json=?,basis_json=?,validation_json=? WHERE batch_id=?',
                   (json.dumps(doc['rows']), json.dumps(basis), '{}', b['id']))
    checked = c.post(base+'/validate', json={'revision': doc['revision']})
    assert checked.status_code == 200, checked.text
    checked = checked.json()
    assert checked['validation']['valid_count'] == 2, checked['validation']
    assert {r['visible']['color'] for r in checked['rows']} == {'5-Tier (2 Pack)', '5-Tier (1 Pack)'}
    assert not checked['variant_groups'][0]['errors'] and checked['variant_groups'][0]['rule_mappings']
    with catalog.connect() as db:
        events = db.execute("SELECT details_json FROM walmart_listing_events WHERE batch_id=? AND action='variant_group_rule'", (b['id'],)).fetchall()
    assert any('user_authorized_custom_fallback' in e[0] and 'variant_rules' in e[0] for e in events)
    response = c.post(base+'/submit', json=submission(checked))
    assert response.status_code == 200, response.text
    wire = remote.feeds[response.json()['feed_id']]['MPItem']
    assert {r['Visible'][PT]['color'] for r in wire} == {'5-Tier (2 Pack)', '5-Tier (1 Pack)'}


def test_previously_submitted_multipack_group_keeps_routing_under_new_rule():
    from catalog.listing_variants import group_id
    rows, basis, schemas = family()
    for n, row in enumerate(rows):
        row['visible'].update(variantGroupId=group_id(1, 'parent'), variantAttributeNames=['multipackQuantity'],
                              multipackQuantity=n+1, isPrimaryVariant='Yes' if n==0 else 'No', color=['Red','Blue'][n])
    history = {r['platform_sku']: {'row': copy.deepcopy(r), 'status': 'failed'} for r in rows}
    latest = {s: {'status': 'failed'} for s in history}
    before = copy.deepcopy(rows)
    group = plan(rows, basis, schemas, history=history, latest=latest, apply=True)[0]
    assert not group['errors'] and group['axes'] == ['multipackQuantity']
    assert rows == before
