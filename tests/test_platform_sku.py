import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from catalog.db import Catalog
from catalog.models import ImportBundle
from catalog.platform_sku import backfill_codes, inferred_code, random_suffix, LEGACY_RULE, RULE
from catalog.prelisting import preview_fingerprint
from test_prelisting import create, inputs, local, product


def variants(batch):
    return [v for item in batch['preview']['items'] for v in item['variants']]


def test_format_adapts_to_store_and_preserves_source(local):
    client, catalog = local
    before = catalog.export()
    settings = inputs(client, filters={'q': 'parent-0'})
    first, payload = create(client, settings)
    first_sku = variants(first)[0]['platform_sku']
    assert re.fullmatch(r'MY17[A-Z0-9]{6}', first_sku)
    assert any(c.isalpha() for c in first_sku[4:]) and any(c.isdigit() for c in first_sku[4:])
    assert variants(first)[0]['sku'] == 'child-0'
    assert variants(first)[0]['asin'] == 'B000000000'
    assert catalog.export() == before
    other = next(s for s in client.get('/api/listing-stores').json() if s['name'] == 'MYZ-L-18')
    second, _ = create(client, settings | {'store_id': other['id']})
    second_sku = variants(second)[0]['platform_sku']
    assert re.fullmatch(r'MY18[A-Z0-9]{6}', second_sku)
    assert first_sku[4:] != second_sku[4:]
    assert client.post('/api/prelisting/batches', json=payload).json() == first
    assert client.post(f"/api/prelisting/batches/{first['id']}/action", json={'action': 'confirm'}).status_code == 200
    client.post(f"/api/prelisting/batches/{first['id']}/action", json={'action': 'cancel'})
    third, _ = create(client, settings)
    assert variants(third)[0]['platform_sku'] == first_sku
    assert Catalog(catalog.path).export() == before
    assert len({v['platform_sku'] for v in variants(second) + variants(third)}) == 2


def test_collision_retries_and_leading_zeroes(local, monkeypatch):
    client, catalog = local
    values = iter(['000A12', '000A12', '000A12', 'Z99999', '000A12', 'Z99999', 'A23456'])
    monkeypatch.setattr('catalog.platform_sku.random_suffix', lambda: next(values))
    batch, _ = create(client, inputs(client))
    skus = [v['platform_sku'] for v in variants(batch)]
    assert skus == ['MY17000A12', 'MY17Z99999', 'MY17A23456']
    with catalog.connect() as db:
        assert db.execute('SELECT COUNT(DISTINCT random_code) FROM platform_skus').fetchone()[0] == 3


def test_exhausted_collision_rolls_back_all_allocations(local, monkeypatch):
    client, catalog = local
    monkeypatch.setattr('catalog.platform_sku.random_suffix', lambda: 'A00042')
    settings = inputs(client)
    preview = client.post('/api/prelisting/preview', json=settings).json()
    result = client.post('/api/prelisting/batches', json=settings | {
        'preview_hash': preview['preview_hash'], 'request_key': str(uuid.uuid4())})
    assert result.status_code == 409
    with catalog.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM platform_skus').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM prelisting_batches').fetchone()[0] == 0


def test_concurrent_distinct_stores_and_global_random_uniqueness(local):
    client, catalog = local
    stores = client.get('/api/listing-stores').json()
    settings = [inputs(client, store_id=next(s['id'] for s in stores if s['name'] == f'MYZ-L-{n}')) for n in (17, 18)]
    payloads = [s | {'preview_hash': client.post('/api/prelisting/preview', json=s).json()['preview_hash'],
                     'request_key': str(uuid.uuid4())} for s in settings]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda p: client.post('/api/prelisting/batches', json=p), payloads))
    assert [r.status_code for r in responses] == [201, 201]
    skus = [v['platform_sku'] for r in responses for v in variants(r.json())]
    assert len(set(s[4:] for s in skus)) == 6
    with catalog.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM platform_skus').fetchone()[0] == 6


def test_reimport_changed_source_sku_reuses_by_asin(local):
    client, catalog = local
    settings = inputs(client, filters={'q': 'parent-0'})
    first, _ = create(client, settings)
    client.post(f"/api/prelisting/batches/{first['id']}/action", json={'action': 'cancel'})
    changed = product(0, observed_at='2026-11-06T00:00:00+00:00')
    changed.variants[0].sku = 'renamed-source-sku'
    changed.variants[0].price_check['sku'] = 'renamed-source-sku'
    catalog.ingest(ImportBundle(namespace='test', label='new-source-sku', listings=[changed]))
    second, _ = create(client, settings)
    assert variants(second)[0]['platform_sku'] == variants(first)[0]['platform_sku']
    assert variants(second)[0]['sku'] == 'renamed-source-sku'


@pytest.mark.parametrize(('name', 'code'), [('MYZ-L-21', '21'), ('MY-W-1', '01'), ('MYZ-L-104', None), ('MY-HDS', None), ('MYZ-L-２１', None)])
def test_store_name_inference(name, code):
    assert inferred_code(name) == code


def test_store_codes_are_explicit_unique_and_locked_after_use(local):
    client, _ = local
    settings = inputs(client)
    stores = client.get('/api/listing-stores').json()
    assert {s['name']: s['sku_code'] for s in stores if s['name'] in ('MY-H-20', 'MYZ-L-20')} == {
        'MY-H-20': 'H2', 'MYZ-L-20': 'L2'}
    new = client.post('/api/listing-stores', json={'name': 'NewStore', 'sku_code': '88'}).json()
    assert new['sku_code'] == '88'
    assert client.post('/api/listing-stores', json={'name': 'Duplicate', 'sku_code': '88'}).status_code == 409
    assert client.patch(f"/api/listing-stores/{new['id']}", json={'sku_code': '17'}).status_code == 409
    create(client, settings)
    assert client.patch(f"/api/listing-stores/{settings['store_id']}", json={'sku_code': '89'}).status_code == 409
    assert client.patch(f"/api/listing-stores/{settings['store_id']}", json={'sku_code': None}).status_code == 409
    assert client.patch(f"/api/listing-stores/{settings['store_id']}", json={'sku_code': '17'}).status_code == 200
    for bad in ['1', '123', 'H_', '-2', '２１', '汉2']:
        assert client.patch(f"/api/listing-stores/{new['id']}", json={'sku_code': bad}).status_code == 422
    missing = client.post('/api/listing-stores', json={'name': 'AnotherStore'}).json()
    result = client.post('/api/prelisting/preview', json=settings | {'store_id': missing['id']})
    assert result.status_code == 409 and '两位 SKU 编码' in result.text


def test_duplicate_child_identity_and_tampered_batch_cannot_confirm(local):
    client, catalog = local
    changed = product(0, observed_at='2026-11-06T00:00:00+00:00')
    child = changed.variants[0].model_copy(deep=True)
    child.sku = 'another-source-sku'
    child.price_check['sku'] = child.sku
    changed.variants.append(child)
    catalog.ingest(ImportBundle(namespace='test', label='duplicate-child', listings=[changed]))
    settings = inputs(client, filters={'q': 'parent-0'})
    assert client.post('/api/prelisting/preview', json=settings).json()['can_create'] is False
    batch, _ = create(client, inputs(client, filters={'q': 'parent-1'}))
    with catalog.connect() as db:
        frozen = batch['preview']
        frozen['items'][0]['variants'][0]['platform_sku'] = 'MY17999999'
        db.execute('UPDATE prelisting_batches SET preview_json=? WHERE id=?', (json.dumps(frozen), batch['id']))
    result = client.post(f"/api/prelisting/batches/{batch['id']}/action", json={'action': 'confirm'})
    assert result.status_code == 409 and '平台 SKU' in result.text


def test_standalone_product_is_assigned_without_editing_its_original_sku(local):
    client, catalog = local
    catalog.ingest(ImportBundle(namespace='test', label='single', listings=[product(4, variants=[])]))
    batch, _ = create(client, inputs(client, filters={'q': 'parent-4'}))
    assert variants(batch)[0]['sku'] == 'parent-4'
    assert re.fullmatch(r'MY17[A-Z0-9]{6}', variants(batch)[0]['platform_sku'])


def test_schema_upgrade_keeps_stores_and_is_idempotent(local):
    client, catalog = local
    before = client.get('/api/listing-stores').json()
    with catalog.connect() as db:
        db.execute('DROP TABLE platform_skus')
        db.execute('DROP INDEX store_sku_code')
        db.execute('ALTER TABLE listing_stores DROP COLUMN sku_code')
        db.execute('DELETE FROM migrations WHERE version=9')
    Catalog(catalog.path)
    Catalog(catalog.path)
    assert client.get('/api/listing-stores').json() == before


def test_alphanumeric_store_codes_normalize_allocate_and_remain_unique(local):
    client, catalog = local
    before = catalog.export()
    store = client.post('/api/listing-stores', json={'name': 'CustomShop', 'sku_code': ' x7 '}).json()
    assert store['sku_code'] == 'X7'
    assert client.post('/api/listing-stores', json={'name': 'AnotherShop', 'sku_code': 'x7'}).status_code == 409
    batch, _ = create(client, inputs(client, store_id=store['id']))
    assert all(re.fullmatch(r'MYX7[A-Z0-9]{6}', v['platform_sku']) for v in variants(batch))
    assert client.patch(f"/api/listing-stores/{store['id']}", json={'sku_code': 'h8'}).status_code == 409
    assert client.post(f"/api/prelisting/batches/{batch['id']}/action", json={'action': 'confirm'}).status_code == 200
    assert catalog.export() == before


def test_new_store_uses_letter_fallback_when_numeric_code_is_taken(local):
    client, _ = local
    first = client.post('/api/listing-stores', json={'name': 'MY-H-17'}).json()
    assert first['sku_code'] == 'H1'
    second = client.post('/api/listing-stores', json={'name': 'Another-H-17'}).json()
    assert second['sku_code'] is None
    assert client.patch(f"/api/listing-stores/{second['id']}", json={'sku_code': 'h1'}).status_code == 409
    assert client.patch(f"/api/listing-stores/{second['id']}", json={'sku_code': 'h7'}).status_code == 200
    assert next(s for s in client.get('/api/listing-stores').json() if s['id'] == second['id'])['sku_code'] == 'H7'


def test_upgrade_fills_only_missing_codes_and_preserves_allocated_skus(local):
    client, catalog = local
    batch, _ = create(client, inputs(client))
    before = catalog.export()
    with catalog.connect() as db:
        db.execute("UPDATE listing_stores SET sku_code=NULL WHERE name IN ('MY-H-20','MYZ-L-20')")
        db.execute('DELETE FROM migrations WHERE version=10')
    Catalog(catalog.path)
    Catalog(catalog.path)
    codes = {s['name']: s['sku_code'] for s in client.get('/api/listing-stores').json()}
    assert codes['MY-H-20'] == 'H2' and codes['MYZ-L-20'] == 'L2'
    assert codes['MYZ-L-17'] == '17'
    assert client.get(f"/api/prelisting/batches/{batch['id']}").json() == batch
    assert catalog.export() == before


def test_ambiguous_letter_fallback_does_not_pick_a_store_arbitrarily(local):
    _, catalog = local
    with catalog.connect() as db:
        for name in ('MY-X-20', 'Other-X-20'):
            db.execute("INSERT INTO listing_stores(name,name_key,marketplace_name,origin) VALUES(?,?,?,'manual')",
                       (name, name.lower(), name))
        backfill_codes(db)
        rows = db.execute("SELECT sku_code FROM listing_stores WHERE name IN ('MY-X-20','Other-X-20')").fetchall()
        assert all(r['sku_code'] is None for r in rows)


def legacy_batch(client, catalog):
    batch, _ = create(client, inputs(client))
    frozen = batch['preview']
    with catalog.connect() as db:
        for index, variant in enumerate(variants(batch)):
            old = variant['platform_sku']
            code = f'{index:06d}'
            variant['platform_sku'] = old[:4] + code
            db.execute('UPDATE platform_skus SET random_code=?,platform_sku=? WHERE platform_sku=?',
                       (code, variant['platform_sku'], old))
        frozen['sku_rule'] = LEGACY_RULE
        frozen['preview_hash'] = preview_fingerprint(frozen)
        db.execute('UPDATE prelisting_batches SET preview_json=? WHERE id=?', (json.dumps(frozen), batch['id']))
    return client.get(f"/api/prelisting/batches/{batch['id']}").json()


def without_target_skus(preview):
    return {k: v for k, v in preview.items() if k not in ('sku_rule', 'preview_hash', 'items')} | {
        'items': [item | {'variants': [{k: v for k, v in variant.items() if k != 'platform_sku'}
                                     for variant in item['variants']]} for item in preview['items']]}


def test_random_suffix_rejects_letters_only_and_digits_only(monkeypatch):
    chars = iter('ABCDEF123456A7K9P3')
    monkeypatch.setattr('catalog.platform_sku.secrets.choice', lambda _: next(chars))
    assert random_suffix() == 'A7K9P3'


def test_all_generated_suffixes_contain_letters_and_digits():
    for _ in range(200):
        suffix = random_suffix()
        assert re.fullmatch(r'[A-Z0-9]{6}', suffix)
        assert any(c.isalpha() for c in suffix) and any(c.isdigit() for c in suffix)


def test_upgrade_draft_preserves_every_other_field_and_is_idempotent(local):
    client, catalog = local
    batch = legacy_batch(client, catalog)
    original = catalog.export()
    route = f"/api/prelisting/batches/{batch['id']}/action"
    response = client.post(route, json={'action': 'upgrade_skus'})
    assert response.status_code == 200, response.text
    upgraded = response.json()
    assert without_target_skus(upgraded['preview']) == without_target_skus(batch['preview'])
    assert upgraded['preview']['sku_rule'] == RULE
    assert all(any(c.isalpha() for c in v['platform_sku'][4:]) for v in variants(upgraded))
    assert client.post(route, json={'action': 'upgrade_skus'}).json() == upgraded
    with catalog.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM prelisting_events WHERE batch_id=? AND action='upgrade_skus'", (batch['id'],)).fetchone()[0] == 1
    assert client.post(route, json={'action': 'confirm'}).status_code == 200
    assert client.post(route, json={'action': 'upgrade_skus'}).status_code == 409
    client.post(route, json={'action': 'cancel'})
    assert client.post(route, json={'action': 'upgrade_skus'}).status_code == 409
    recreated, _ = create(client, inputs(client))
    assert [v['platform_sku'] for v in variants(recreated)] == [v['platform_sku'] for v in variants(upgraded)]
    assert catalog.export() == original


def test_upgrade_rejects_shared_confirmed_mapping_without_changes(local):
    client, catalog = local
    batch = legacy_batch(client, catalog)
    with catalog.connect() as db:
        db.execute('''INSERT INTO prelisting_batches
            SELECT 'shared-confirmed', 'shared-request', request_hash, 'confirmed', created_at, updated_at,
            actor_id, settings_json, preview_json FROM prelisting_batches WHERE id=?''', (batch['id'],))
    response = client.post(f"/api/prelisting/batches/{batch['id']}/action", json={'action': 'upgrade_skus'})
    assert response.status_code == 409
    assert client.get(f"/api/prelisting/batches/{batch['id']}").json() == batch
    with catalog.connect() as db:
        assert all(r[0].isdigit() for r in db.execute('SELECT random_code FROM platform_skus'))


def test_upgrade_collision_failure_rolls_back_old_reservations(local, monkeypatch):
    client, catalog = local
    batch = legacy_batch(client, catalog)
    monkeypatch.setattr('catalog.platform_sku.random_suffix', lambda: 'A00042')
    response = client.post(f"/api/prelisting/batches/{batch['id']}/action", json={'action': 'upgrade_skus'})
    assert response.status_code == 409
    assert client.get(f"/api/prelisting/batches/{batch['id']}").json() == batch
    with catalog.connect() as db:
        assert {r[0] for r in db.execute('SELECT platform_sku FROM platform_skus')} == {v['platform_sku'] for v in variants(batch)}


def test_numeric_schema_migrates_without_changing_existing_reservations(local):
    client, catalog = local
    batch = legacy_batch(client, catalog)
    with catalog.connect() as db:
        check = "random_code ~ '^[0-9]{6}$'" if catalog.postgres else "random_code NOT GLOB '*[^0-9]*'"
        if catalog.postgres:
            db.execute('ALTER TABLE platform_skus DROP CONSTRAINT platform_skus_random_code_check')
            db.execute(f'ALTER TABLE platform_skus ADD CONSTRAINT platform_skus_random_code_check CHECK(length(random_code)=6 AND {check})')
        else:
            db.execute(f'''CREATE TABLE legacy_skus(
                store_id INTEGER NOT NULL REFERENCES listing_stores(id), source_key TEXT NOT NULL,
                identity_key TEXT NOT NULL, random_code TEXT NOT NULL UNIQUE CHECK(length(random_code)=6 AND {check}),
                platform_sku TEXT NOT NULL UNIQUE CHECK(length(platform_sku)=10), created_at TEXT NOT NULL,
                PRIMARY KEY(store_id,source_key,identity_key))''')
            db.execute('INSERT INTO legacy_skus SELECT * FROM platform_skus')
            db.execute('DROP TABLE platform_skus')
            db.execute('ALTER TABLE legacy_skus RENAME TO platform_skus')
        db.execute('DELETE FROM migrations WHERE version=11')
    Catalog(catalog.path)
    Catalog(catalog.path)
    assert client.get(f"/api/prelisting/batches/{batch['id']}").json() == batch
    assert client.post(f"/api/prelisting/batches/{batch['id']}/action", json={'action': 'upgrade_skus'}).status_code == 200
