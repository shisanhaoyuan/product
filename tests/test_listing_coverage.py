from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.db import Catalog
from catalog.models import ImportBundle
from test_prelisting import product, inputs


def seed(catalog):
    rows = [product(0), product(0, external_id='another-store', store='MYZ-L-22',
                              uploaded_at='2026-11-05'),
            product(0, external_id='same-store-again', template='second-template'),
            product(1, uploaded_at='2026-11-06')]
    bundle = ImportBundle(namespace='test', label='test', listings=rows)
    catalog.ingest(bundle)
    catalog.ingest(bundle)
    newer = rows[0].model_copy(update={'observed_at': '2026-11-05T10:00:00+00:00'})
    catalog.ingest(ImportBundle(namespace='test', label='refresh', listings=[newer]))


def test_deduplicated_pagination_and_global_counts(tmp_path):
    catalog = Catalog(tmp_path / 'test.db')
    seed(catalog)
    before = catalog.export()
    first = catalog.find(deduplicate=True, page_size=1)
    second = catalog.find(deduplicate=True, page_size=1, page=2)
    assert first['total'] == second['total'] == 2
    assert first['items'][0]['asin'] == 'B000000001'
    row = second['items'][0]
    assert row['asin'] == 'B000000000' and row['store'] == 'MYZ-L-22'
    assert row['listing_count'] == 3
    assert row['listing_stores'] == [dict(store='MYZ-L-21', count=2), dict(store='MYZ-L-22', count=1)]
    filtered = catalog.find(deduplicate=True, store='MYZ-L-21', q='B000000000')
    assert filtered['total'] == 1
    assert filtered['items'][0]['store'] == 'MYZ-L-21'
    assert filtered['items'][0]['listing_stores'] == row['listing_stores']
    assert catalog.find(deduplicate=True, q='missing')['total'] == 0
    assert catalog.find()['total'] == 4
    assert catalog.stats()['unique_counts'] == dict(all_products=2, complete=2, review=0)
    assert catalog.export() == before


def test_missing_asins_are_not_collapsed_and_namespace_templates_count(tmp_path):
    catalog = Catalog(tmp_path / 'test.db')
    catalog.ingest(ImportBundle(namespace='test', label='test', listings=[
        product(0, asin=''), product(1, asin=''), product(2)]))
    catalog.ingest(ImportBundle(namespace='other', label='test', listings=[product(2)]))
    result = catalog.find(deduplicate=True)
    assert result['total'] == 3
    assert sorted(r['listing_count'] for r in result['items']) == [1, 1, 2]


def test_api_and_filtered_prelisting_share_representatives(tmp_path, monkeypatch):
    monkeypatch.delenv('CATALOG_AUTH_ENABLED', raising=False)
    app = create_app(tmp_path / 'test.db')
    seed(app.state.catalog)
    client = TestClient(app)
    rows = client.get('/api/products?deduplicate=true').json()
    assert rows['total'] == 2
    plan = client.post('/api/prelisting/preview', json=inputs(client, filters={'deduplicate': 'true'}))
    assert plan.status_code == 200, plan.text
    preview = plan.json()
    assert preview['total'] == 2 and preview['can_create']
    assert [row['listing_id'] for row in preview['items']] == [row['id'] for row in rows['items']]
    assert client.get('/api/products').json()['total'] == 4
