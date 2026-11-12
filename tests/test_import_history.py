import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.db import Catalog
from catalog.models import ImportBundle


def payload(batch=None, path='folder/a.json', external_id='one', namespace='test'):
    data = dict(namespace=namespace, label='原始来源', listings=[dict(
        external_id=external_id, asin='B012345678', sku=external_id,
        store='MYZ-L-21', template=external_id, observed_at='2026-11-12T01:00:00Z', evidence_kind='source_only')])
    if batch:
        data['import_context'] = dict(batch_id=batch, file_id=str(uuid4()),
            name='folder', kind='folder', path=path)
    return data


def test_folder_summary_preserves_files_counts_and_product_evidence(tmp_path):
    catalog = Catalog(tmp_path/'test.db')
    batch = str(uuid4())
    first = ImportBundle.model_validate(payload(batch))
    catalog.ingest(first)
    original = catalog.export()
    second = ImportBundle.model_validate(payload(batch, path='folder/nested/b.json'))
    catalog.ingest(second)
    # Same folder name in a new upload remains a separate batch.
    catalog.ingest(ImportBundle.model_validate(payload(str(uuid4()))))
    rows = catalog.imports()
    assert len(rows) == 2
    assert rows[0]['file_count'] == 1
    assert rows[1]['file_count'] == 2 and rows[1]['kind'] == 'folder'
    assert rows[1]['label'] == 'folder'
    assert rows[1]['result']['created'] == rows[1]['result']['unchanged'] == 1
    assert {f['path'] for f in rows[1]['files']} == {'folder/a.json', 'folder/nested/b.json'}
    assert catalog.export() == original
    assert 'import_context' not in first.model_dump()


def test_retry_after_lost_response_is_idempotent_and_rejects_changed_payload(tmp_path):
    catalog = Catalog(tmp_path/'test.db')
    data = payload(str(uuid4()))
    bundle = ImportBundle.model_validate(data)
    first = catalog.ingest(bundle, actor_id=None)
    assert catalog.ingest(bundle) == first
    assert catalog.imports()[0]['file_count'] == 1
    data['listings'][0]['external_id'] = 'changed'
    with pytest.raises(ValueError, match='批次标识已使用'):
        catalog.ingest(ImportBundle.model_validate(data))
    assert catalog.stats()['listings'] == 1


def test_failed_file_does_not_count_then_success_joins_original_batch(tmp_path):
    client = TestClient(create_app(tmp_path/'test.db'))
    batch = str(uuid4())
    assert client.post('/api/imports', json=payload(batch)).status_code == 200
    data = payload(batch, path='folder/b.json')
    data['listings'][0]['asin'] = 'B099999999'  # Existing external ID identity conflict.
    assert client.post('/api/imports', json=data).status_code == 409
    assert client.get('/api/imports').json()[0]['file_count'] == 1
    data['listings'][0]['asin'] = 'B012345678'
    assert client.post('/api/imports', json=data).status_code == 200
    assert client.get('/api/imports').json()[0]['file_count'] == 2
    data['import_context']['batch_id'] = 'invalid'
    assert client.post('/api/imports', json=data).status_code == 422


def test_legacy_grouping_and_limit_apply_to_groups_not_files(tmp_path):
    catalog = Catalog(tmp_path/'test.db')
    with catalog.connect() as db:
        records = []
        for day, namespace, label, count in [
            ('2026-11-10', 'test', 'folder', 1),
            ('2026-11-11', 'test', 'folder', 120),
            ('2026-11-11', 'other', 'folder', 1),
            ('2026-11-11', 'test', 'different', 1),
        ]:
            records.extend((day+'T01:00:00+00:00', namespace, label, 'digest', json.dumps({'created': 1}))
                           for _ in range(count))
        db.executemany('INSERT INTO imports(at,namespace,label,digest,result_json) VALUES(?,?,?,?,?)', records)
    rows = catalog.imports()
    assert len(rows) == 4
    assert rows[2]['kind'] == 'legacy' and rows[2]['file_count'] == 120
    assert rows[2]['result']['created'] == 120
    with catalog.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM imports').fetchone()[0] == 123


def test_actor_scopes_batch_identity(tmp_path):
    catalog = Catalog(tmp_path/'test.db')
    from catalog.auth import AuthStore
    AuthStore(catalog)
    bundle = ImportBundle.model_validate(payload(str(uuid4())))
    catalog.ingest(bundle, actor_id=10)
    catalog.ingest(bundle, actor_id=11)
    assert len(catalog.imports()) == 2
