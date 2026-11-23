import copy
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.models import ImportBundle
from catalog.listing_variants import plan, group_id, submission_errors, SYSTEM_FIELDS
from catalog.walmart_listing_schema import sections, DEFAULT_VERSION, feed_item, precise_validator
from catalog.walmart_publishing_client import WalmartPublishingClient
from test_prelisting import product, inputs, create
from test_walmart_accounts import vault, Remote, bind
from test_walmart_publishing import spec, PT, PublisherRemote, ready, send, submission, publishing
from test_giga_prelisting import warehouse
from test_giga import settings


def variant_spec():
    schema = spec()
    props = sections(schema, PT)['visible']['properties']
    props.update(variantGroupId={'type': 'string', 'minLength': 1},
                 variantAttributeNames={'type': 'array', 'minItems': 1, 'maxItems': 2,
                                        'items': {'enum': ['color', 'multipackQuantity']}},
                 isPrimaryVariant={'enum': ['Yes', 'No']}, color={'type': 'string', 'minLength': 1},
                 multipackQuantity={'type': 'integer', 'minimum': 1},
                 swatchImages={'type': 'array', 'items': {'type': 'object', 'properties': {
                     'swatchVariantAttribute': {'enum': ['color']}, 'swatchImageUrl': {'type': 'string', 'format': 'uri'}},
                     'required': ['swatchVariantAttribute', 'swatchImageUrl'], 'additionalProperties': False}})
    return schema


def family(values=('Red', 'Blue')):
    rows = [dict(platform_sku=f'SKU-{n}', source_sku=f'child-{n}', listing_id='parent', product_type=PT,
                 visible={'brand': 'Brand', 'mainImageUrl': f'https://example.com/{n}.jpg'}) for n in range(len(values))]
    basis = dict(store={'id': 1}, items=[dict(listing_id='parent', variants=[
        dict(platform_sku=r['platform_sku'], sku=r['source_sku'], content={'attributes': {'Color': value}})
        for r, value in zip(rows, values)])])
    return rows, basis, {PT: variant_spec()}


def test_source_parent_group_is_stable_unique_and_not_title_based():
    rows, basis, schemas = family()
    group = plan(rows, basis, schemas, apply=True)[0]
    assert not group['errors'] and group['axes'] == ['color']
    assert {r['visible']['variantGroupId'] for r in rows} == {group_id(1, 'parent')}
    assert [r['visible']['isPrimaryVariant'] for r in rows] == ['Yes', 'No']
    assert rows[0]['visible']['swatchImages'][0]['swatchImageUrl'].endswith('/0.jpg')
    assert rows[1]['visible']['swatchImages'][0]['swatchImageUrl'].endswith('/1.jpg')
    before = copy.deepcopy(rows)
    assert not plan(rows, basis, schemas, apply=True)[0]['errors'] and rows == before
    for r in rows:
        for key in SYSTEM_FIELDS:
            r['visible'].pop(key)
    basis['store']['id'] = 2
    assert plan(rows, basis, schemas, apply=True)[0]['group_id'] != group['group_id']
    # Similar-looking products under different source parents remain independent.
    rows, basis, schemas = family()
    rows[1]['listing_id'] = 'other'
    basis['items'].append(dict(listing_id='other', variants=[basis['items'][0]['variants'].pop()]))
    assert plan(rows, basis, schemas, apply=True) == []


def test_shared_attributes_fill_each_child_despite_family_manual_flag():
    rows, basis, schemas = family()
    props = sections(schemas[PT], PT)['visible']['properties']
    props.update(finish={'type': 'string'}, material={'type': 'array', 'items': {'type': 'string'}})
    for row in rows:
        row['variant_manual'] = True
        row['visible']['variantAttributeNames'] = ['color']
    rows[0]['visible']['color'] = 'User Red'
    rows[0]['variant_reviewed_fields'] = ['color', 'finish']
    basis['items'][0]['variants'][0]['content']['attributes'].update(
        Finish='Do not restore cleared field', Material=['Wood', 'Metal'])
    basis['items'][0]['variants'][1]['content']['attributes'].update(
        Finish={'variant_value': 'Glossy'}, Material='Plastic')
    plan(rows, basis, schemas, apply=True)
    assert rows[0]['visible']['color'] == 'User Red'
    assert 'finish' not in rows[0]['visible']
    assert rows[0]['visible']['material'] == ['Wood', 'Metal']
    assert rows[1]['visible']['color'] == 'Blue'
    assert rows[1]['visible']['finish'] == 'Glossy'
    assert rows[1]['visible']['material'] == ['Plastic']
    snapshot = copy.deepcopy(rows)
    plan(rows, basis, schemas, apply=True)
    assert rows == snapshot
    assert all(row['visible']['variantAttributeNames'] == ['color'] for row in rows)


def test_shared_attributes_respect_row_schema_submission_and_missing_source():
    rows, basis, schemas = family()
    other = 'Other Type'
    other_schema = copy.deepcopy(schemas[PT])
    visible = other_schema['properties']['MPItem']['items']['properties']['Visible']['properties']
    visible[other] = visible.pop(PT)
    visible[other]['properties']['color'] = {'type': 'array', 'items': {'type': 'string'}}
    schemas[other] = other_schema
    rows[1]['product_type'] = other
    plan(rows, basis, schemas, latest={rows[0]['platform_sku']: {'status': 'published'}}, apply=True)
    assert 'color' not in rows[0]['visible']
    assert rows[1]['product_type'] == PT
    assert rows[1]['visible']['color'] == 'Blue'
    rows[1]['visible'].pop('color')
    basis['items'][0]['variants'][1]['content']['attributes'] = {}
    plan(rows, basis, schemas, latest={rows[0]['platform_sku']: {'status': 'published'}}, apply=True)
    assert 'color' not in rows[1]['visible']


def test_family_type_repairs_blanks_and_explicit_choice_updates_editable_siblings():
    from catalog.listing_variants import synchronize_types
    rows, _, _ = family()
    rows[0]['product_type'] = ''
    synchronize_types(rows)
    assert {r['product_type'] for r in rows} == {PT}
    unrelated = copy.deepcopy(rows[0]); unrelated.update(listing_id='other', platform_sku='other')
    rows.append(unrelated)
    synchronize_types(rows, preferred={'parent': 'End Tables'})
    assert [r['product_type'] for r in rows] == ['End Tables', 'End Tables', PT]
    synchronize_types(rows, preferred={'parent': ''})
    assert [r['product_type'] for r in rows] == ['', '', PT]


def test_family_type_uses_submitted_history_and_rejects_incompatible_edits():
    from catalog.listing_variants import synchronize_types
    rows, _, _ = family()
    historical = copy.deepcopy(rows[0]); historical['platform_sku'] = 'historical'
    history = {'historical': {'row': historical, 'status': 'published'}}
    rows[0]['product_type'] = ''
    rows[1]['product_type'] = 'Different'
    synchronize_types(rows, history=history)
    assert {r['product_type'] for r in rows} == {PT}
    with pytest.raises(ValueError, match='沿用'):
        synchronize_types(rows, history=history, preferred={'parent': 'Different'})


@pytest.mark.parametrize('old_type', ['', 'End Tables'])
def test_prepare_repairs_existing_blank_family_types_and_save_shares_choice(variant_publishing, old_type):
    c, catalog, _, _, batch, base, _ = variant_publishing
    doc = ready(variant_publishing)
    doc['rows'][1]['product_type'] = old_type
    with catalog.connect() as db:
        db.execute('UPDATE walmart_listing_documents SET rows_json=? WHERE batch_id=?',
                   (json.dumps(doc['rows']), batch['id']))
    response = c.post(base+'/prepare')
    assert response.status_code == 200, response.text
    repaired = response.json()
    assert all(r['product_type'] == PT for r in repaired['rows'])
    assert repaired['specs'][PT]['sections']['visible']['properties']
    repaired['rows'][0]['product_type'] = 'End Tables'
    response = c.patch(base, json={k: repaired[k] for k in ('revision', 'version', 'rows')})
    assert response.status_code == 200, response.text
    assert {r['product_type'] for r in response.json()['rows']} == {'End Tables'}
    assert {r['product_type'] for r in c.get(base).json()['rows']} == {'End Tables'}


def test_six_variant_legacy_mixed_types_are_reconciled_before_attributes():
    rows, basis, schemas = family(('Red', 'Blue', 'Green', 'White', 'Black', 'Brown'))
    for row, pt in zip(rows, ['', PT, 'End Tables', '', PT, '']):
        row['product_type'] = pt
        row['variant_manual'] = True
        row['visible']['variantAttributeNames'] = ['color']
    snapshot = copy.deepcopy(rows)
    group = plan(rows, basis, schemas, apply=True)[0]
    assert {r['product_type'] for r in rows} == {PT}
    assert [r['visible']['color'] for r in rows] == ['Red', 'Blue', 'Green', 'White', 'Black', 'Brown']
    assert not group['errors'], group['errors']
    assert group['allowed_axes'] == ['color']
    for before, after in zip(snapshot, rows):
        assert before['platform_sku'] == after['platform_sku']
        assert before['source_sku'] == after['source_sku']
        assert before['visible']['mainImageUrl'] == after['visible']['mainImageUrl']
    saved = copy.deepcopy(rows)
    assert not plan(rows, basis, schemas, apply=True)[0]['errors']
    assert rows == saved


def test_family_type_tie_uses_primary_and_multiple_locked_types_still_block():
    from catalog.listing_variants import synchronize_types
    rows, _, _ = family()
    rows[1]['product_type'] = 'End Tables'
    rows[1]['visible']['isPrimaryVariant'] = 'Yes'
    synchronize_types(rows)
    assert {r['product_type'] for r in rows} == {'End Tables'}
    rows[0]['product_type'] = PT
    synchronize_types(rows, product_types={PT})
    assert {r['product_type'] for r in rows} == {PT}
    rows[1]['product_type'] = 'End Tables'
    rows[0]['product_type'] = PT
    original = copy.deepcopy(rows)
    synchronize_types(rows, latest={r['platform_sku']: {'status': 'published'} for r in rows})
    assert rows == original


def test_duplicate_and_unsupported_discriminators_do_not_silently_become_standalone():
    rows, basis, schemas = family(('Brown', 'Brown'))
    result = plan(rows, basis, schemas, apply=True)[0]
    assert result['errors'] and not result['axes']
    # The Skill keeps factual pack values in a custom field; no invented discriminator.
    for n, variant in enumerate(basis['items'][0]['variants']):
        variant['content']['attributes']['Item Package Quantity'] = str(n+1)
    result = plan(rows, basis, schemas, apply=True)[0]
    assert not result['errors'] and result['axes'] == ['color']
    assert [r['visible']['color'] for r in rows] == ['Brown', 'Brown**2']
    assert 'multipackQuantity' not in result['allowed_axes']
    sections(schemas[PT], PT)['visible']['properties']['variantAttributeNames']['items']['enum'] = ['color']
    assert not plan(rows, basis, schemas, apply=True)[0]['errors']


def test_combinations_require_more_than_one_axis_and_missing_values_block():
    rows, basis, schemas = family(('Red', 'Red', 'Blue', 'Blue'))
    for n, v in enumerate(basis['items'][0]['variants']):
        v['content']['attributes']['multipackQuantity'] = n % 2 + 1
    group = plan(rows, basis, schemas, apply=True)[0]
    assert not group['errors'] and group['axes'] == ['color']
    rows[0]['visible'].pop('color')
    assert any('颜色' in e['message'] for e in plan(rows, basis, schemas)[0]['errors'])
    rows[0]['visible']['color'] = rows[1]['visible']['color']
    assert any('重复' in e['message'] for e in plan(rows, basis, schemas)[0]['errors'])


def test_partial_selection_retry_primary_and_history_are_guarded():
    rows, basis, schemas = family()
    groups = plan(rows, basis, schemas, apply=True)
    assert submission_errors(groups, ['SKU-0'], {}, {})
    assert not submission_errors(groups, ['SKU-0', 'SKU-1'], {}, {})
    history = {r['platform_sku']: {'row': copy.deepcopy(r), 'status': 'success' if n == 0 else 'failed'} for n, r in enumerate(rows)}
    latest = {k: {'status': v['status']} for k, v in history.items()}
    groups = plan(rows, basis, schemas, history=history, latest=latest, apply=True)
    assert not groups[0]['errors'] and not submission_errors(groups, ['SKU-1'], latest, history)
    rows[1]['visible']['isPrimaryVariant'] = 'Yes'
    assert any('主变体' in e['message'] for e in plan(rows, basis, schemas, history=history, latest=latest, apply=True)[0]['errors'])
    rows, basis, schemas = family()
    basis['items'][0]['variants'].pop(); rows.pop()
    groups = plan(rows, basis, schemas, sources={'parent': {'variants': [1, 2]}}, apply=True)
    assert any('至少两个' in e['message'] for e in groups[0]['errors'])


def test_history_from_another_batch_participates_in_uniqueness_and_legacy_guard():
    rows, basis, schemas = family()
    plan(rows, basis, schemas, apply=True)
    old = copy.deepcopy(rows[0])
    history = {'SKU-0': {'row': old, 'status': 'success'}}
    rows = rows[1:]; basis['items'][0]['variants'] = basis['items'][0]['variants'][1:]
    group = plan(rows, basis, schemas, history=history, apply=True)[0]
    assert not group['errors'] and group['primary_sku'] == 'SKU-0'
    rows[0]['visible']['color'] = 'Red'
    assert any('重复' in e['message'] for e in plan(rows, basis, schemas, history=history)[0]['errors'])
    for key in SYSTEM_FIELDS:
        old['visible'].pop(key)
    assert any('单品提交' in e['message'] for e in plan(rows, basis, schemas, history=history)[0]['errors'])


def test_actual_official_schema_axis_shape_and_local_swatch_urls(vault):
    schema = json.loads((Path(__file__).parent/'fixtures/walmart/sofas-mp-item-20260829.json').read_text())
    rows, basis, _ = family()
    for row in rows:
        row['product_type'] = 'Sofas & Couches'
        row.update(identification={'mode': 'identifier', 'type': 'UPC', 'value': '012345678905'}, orderable={}, extra={})
        row['visible']['mainImageUrl'] = '/api/products/images/assets/' + 'a'*64
    group = plan(rows, basis, {'Sofas & Couches': schema}, apply=True)[0]
    assert not group['errors']
    props = sections(schema, 'Sofas & Couches')['visible']['properties']
    for row in rows:
        for key in (*SYSTEM_FIELDS, 'color', 'swatchImages'):
            assert precise_validator(props[key]).is_valid(row['visible'][key])
        wire, _ = feed_item(row, schema, DEFAULT_VERSION, 'https://catalog.example.test')
        assert wire['Visible']['Sofas & Couches']['swatchImages'][0]['swatchImageUrl'].startswith('https://catalog.example.test/')


@pytest.fixture
def variant_publishing(tmp_path, vault):
    return variant_publishing_fixture(tmp_path, vault)


def variant_publishing_fixture(tmp_path, vault, count=2):
    app = create_app(tmp_path/'variants.db'); client = TestClient(app); catalog = app.state.catalog
    children = []
    for n in range(count):
        child = product(n).variants[0].model_dump()
        digits = f'0123456789{n:01d}'
        code = digits + str(-sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(digits)) % 10)
        child.update(attributes={'Color': ['Red', 'Blue', 'Green', 'White', 'Black', 'Brown'][n]}, ean=code,
                     images=[f'https://example.com/{n}.jpg'], shipping_weight='10',
                     copy={'title': 'NewBrand Chair', 'description': 'A wooden chair', 'features': ['Wooden seat']})
        children.append(child)
    catalog.ingest(ImportBundle(namespace='test', label='variants', listings=[product(0, brand='NewBrand', variants=children)]))
    b, _ = create(client, inputs(client))
    Remote().install(app); bind(client, b['preview']['store']['id'])
    remote = PublisherRemote(); remote.schema = variant_spec()
    app.state.walmart_publisher = WalmartPublishingClient(httpx.MockTransport(remote.handler))
    base = f"/api/prelisting/batches/{b['id']}/walmart"
    response = client.post(base+'/prepare'); assert response.status_code == 200, response.text
    return client, catalog, None, None, b, base, remote


def test_api_submit_groups_actual_payload_and_retries_failed_child(variant_publishing):
    c, catalog, _, _, _, base, remote = variant_publishing
    doc = ready(variant_publishing)
    assert doc['validation']['valid_count'] == 2, doc['validation']
    assert len(doc['variant_groups']) == 1 and not doc['variant_groups'][0]['errors']
    skus = [r['platform_sku'] for r in doc['rows']]
    response = c.post(base+'/submit', json=submission(doc, skus=skus[:1]))
    assert response.status_code == 409 and not remote.posts
    submitted = send(variant_publishing, doc)
    wire = remote.feeds[submitted['feed_id']]['MPItem']
    assert len({r['Visible'][PT]['variantGroupId'] for r in wire}) == 1
    assert sorted(r['Visible'][PT]['isPrimaryVariant'] for r in wire) == ['No', 'Yes']
    assert len({r['Orderable']['productIdentifiers']['productId'] for r in wire}) == 2
    # No live platform changes: HTTP fake reports one item rejected, one published.
    failed = next(r['Orderable']['sku'] for r in wire if r['Visible'][PT]['isPrimaryVariant'] == 'No')
    remote.item_status[failed] = 'DATA_ERROR'
    response = c.post(base+f"/attempts/{submitted['id']}/refresh")
    assert response.status_code == 200, response.text
    doc = c.get(base).json()
    row = next(r for r in doc['rows'] if r['platform_sku'] == failed)
    row['visible']['color'] = 'Green'
    response = c.patch(base, json={k: doc[k] for k in ('revision', 'version', 'rows')}); assert response.status_code == 200, response.text
    doc = response.json()
    response = c.post(base+'/validate', json={'revision': doc['revision']}); assert response.status_code == 200, response.text
    doc = response.json(); assert doc['validation']['rows'][failed]['valid'], doc['validation']
    retry = send(variant_publishing, doc, skus=[failed])
    repeated = remote.feeds[retry['feed_id']]['MPItem']
    assert len(repeated) == 1 and repeated[0]['Visible'][PT]['isPrimaryVariant'] == 'No'
    assert repeated[0]['Visible'][PT]['variantGroupId'] == wire[0]['Visible'][PT]['variantGroupId']


def test_old_validation_policy_is_invalidated_and_single_products_unchanged(publishing):
    c, catalog, _, _, b, base, remote = publishing
    doc = ready(publishing)
    assert doc['variant_groups'] == []
    with catalog.connect() as db:
        saved = json.loads(db.execute('SELECT validation_json FROM walmart_listing_documents WHERE batch_id=?', (b['id'],)).fetchone()[0])
        saved.pop('variant_policy')
        db.execute('UPDATE walmart_listing_documents SET validation_json=? WHERE batch_id=?', (json.dumps(saved), b['id']))
    assert not c.get(base).json()['validation']['current']
    assert c.post(base+'/submit', json=submission(doc)).status_code == 409


def test_cross_store_template_regenerates_family_routing(variant_publishing):
    from test_listing_templates import target
    c, _, _, _, _, base, _ = variant_publishing
    original = ready(variant_publishing)
    args = target(variant_publishing, brand='NewBrand')
    new, _ = create(c, args)
    new_base = f"/api/prelisting/batches/{new['id']}/walmart"
    cloned = c.get(new_base).json()
    assert all(not any(k in r['visible'] for k in SYSTEM_FIELDS) for r in cloned['rows'])
    assert cloned['variant_groups'][0]['group_id'] != original['variant_groups'][0]['group_id']
    assert {r['source_sku']: r['visible']['color'] for r in cloned['rows']} == {r['source_sku']: r['visible']['color'] for r in original['rows']}
    assert c.get(base).json()['rows'] == original['rows']


def test_generation_final_group_normalization_advances_job_revision(variant_publishing, monkeypatch):
    from test_listing_generation import AI
    from test_images import picture
    from catalog.listing_generation_jobs import start_job, run_job
    from catalog.walmart_publishing import document
    c, catalog, _, _, b, base, _ = variant_publishing
    doc = ready(variant_publishing)
    # Simulate older saved drafts with a type but no variant fields, before spec refresh.
    for row in doc['rows']:
        for key in (*SYSTEM_FIELDS, 'color', 'swatchImages'):
            row['visible'].pop(key, None)
    with catalog.connect() as db:
        db.execute('UPDATE walmart_listing_documents SET rows_json=?,validation_json=? WHERE batch_id=?', (json.dumps(doc['rows']), '{}', b['id']))
        db.execute("DELETE FROM walmart_listing_metadata WHERE kind='spec'")
    def collected(catalog, job, report, progress, checked):
        with catalog.connect() as db:
            return document(db, job['batch_id'])
    monkeypatch.setattr('catalog.listing_preparation_source.collect_before_generation', collected)
    monkeypatch.setattr('catalog.listing_vision.fetch_image', lambda url: picture())
    monkeypatch.setenv('CATALOG_PUBLIC_ORIGIN', 'https://catalog.example.test')
    ai = AI(); publisher = c.app.state.walmart_publisher
    job = start_job(catalog, publisher, b['id'], ai=ai, schedule=False, source_mode='server')
    run_job(catalog, publisher, job['id'], ai)
    final = c.get(base+'/generation').json()['job']
    assert final['state'] == 'complete', final
    saved = c.get(base).json()
    assert final['revision'] == saved['revision'] and saved['revision'] > job['revision']
    assert not saved['variant_groups'][0]['errors']
    assert len({r['visible']['variantGroupId'] for r in saved['rows']}) == 1


@pytest.mark.parametrize('fail_second', [False, True])
def test_generation_selects_type_once_and_keeps_it_for_every_child(variant_publishing, monkeypatch, fail_second):
    from test_listing_generation import AI
    from test_images import picture
    from catalog.listing_ai_client import ListingAIError
    from catalog.listing_generation import source_facts
    from catalog.listing_generation_jobs import start_job, run_job
    from catalog.walmart_publishing import document
    c, catalog, _, _, batch, base, remote = variant_publishing
    doc = ready(variant_publishing)
    for row in doc['rows']:
        row['product_type'] = ''
    with catalog.connect() as db:
        db.execute('UPDATE walmart_listing_documents SET rows_json=?,validation_json=? WHERE batch_id=?',
                   (json.dumps(doc['rows']), '{}', batch['id']))
    def collected(catalog, job, report, progress, checked):
        with catalog.connect() as db:
            return document(db, job['batch_id'])
    choices = []
    def choose(row, item, facts, types, ai):
        choices.append(row['platform_sku'])
        return PT, dict(kind='ai_source', reason='整组家具类目', refs=['category'])
    def facts(catalog, source, item, variant, ai):
        if fail_second and variant['platform_sku'] == doc['rows'][1]['platform_sku']:
            raise ListingAIError('模拟第二个子体资料采集失败')
        return source_facts(source, item, variant), {'state': 'complete'}
    monkeypatch.setattr('catalog.listing_preparation_source.collect_before_generation', collected)
    monkeypatch.setattr('catalog.listing_generation_jobs.choose_type', choose)
    monkeypatch.setattr('catalog.listing_generation_jobs.supplement_facts', facts)
    monkeypatch.setattr('catalog.listing_vision.fetch_image', lambda url: picture())
    monkeypatch.setenv('CATALOG_PUBLIC_ORIGIN', 'https://catalog.example.test')
    ai = AI(); publisher = c.app.state.walmart_publisher
    job = start_job(catalog, publisher, batch['id'], ai=ai, schedule=False, source_mode='server')
    run_job(catalog, publisher, job['id'], ai)
    final = c.get(base+'/generation').json()['job']
    assert final['state'] == 'complete', final
    assert len(choices) == 1
    assert {r['product_type'] for r in c.get(base).json()['rows']} == {PT}
    assert not remote.posts


def test_equivalent_numeric_and_array_values_cannot_disguise_duplicate_variants():
    from catalog.listing_variants import value_key, valid_attribute
    assert value_key(1) == value_key(1.0)
    assert value_key(['Blue', 'Red']) == value_key([' red ', 'blue'])
    assert not valid_attribute({'$ref': 'https://untrusted.invalid/schema'}, 'value')
