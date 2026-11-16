import csv
import io

import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.models import ImportBundle
from catalog.product_search import parse_identifiers
from test_prelisting import product, inputs


@pytest.fixture
def library(tmp_path):
    app = create_app(tmp_path / 'batch-search.db')
    catalog = app.state.catalog
    items = [product(i, platform_sku=f'MY21ABC00{i}') for i in range(4)]
    items[0].variants[0].asin = 'B900000000'
    items[2].copy.title = 'child-0 B000000001 MY21ABC000'  # Text is not an identifier.
    items[3].variants[0].sku = 'child-01'  # Prefix is not an exact match.
    catalog.ingest(ImportBundle(namespace='batch', label='batch', listings=items))
    return TestClient(app), catalog


def test_exact_parent_variant_platform_and_case_insensitive_search(library):
    client, catalog = library
    before = catalog.export()
    for identifier in ['B000000000', 'B900000000', 'parent-0', 'child-0', 'my21abc000']:
        result = client.get('/api/products', params={'identifiers': identifier}).json()
        assert result['total'] == 1 and result['items'][0]['sku'] == 'parent-0'
    mixed = client.get('/api/products', params={
        'identifiers': 'CHILD-0， b000000001\nchild-0; UNKNOWN', 'deduplicate': True}).json()
    assert mixed['total'] == 2
    assert {item['sku'] for item in mixed['items']} == {'parent-0', 'parent-1'}
    assert client.get('/api/products', params={'identifiers': 'child-'}).json()['total'] == 0
    assert client.get('/api/products', params={'identifiers': "' OR 1=1 --"}).json()['total'] == 0
    assert catalog.export() == before


def test_batch_filters_pagination_export_and_existing_text_search(library):
    client, catalog = library
    query = {'identifiers': 'child-0 child-1 child-2', 'page_size': 1, 'source': 'Amazon'}
    first = client.get('/api/products', params=query).json()
    second = client.get('/api/products', params=query | {'page': 2}).json()
    assert first['total'] == second['total'] == 3
    assert first['items'][0]['id'] != second['items'][0]['id']
    assert not client.get('/api/products', params=query | {'page': 4}).json()['items']
    assert client.get('/api/products', params=query | {'brand': 'missing'}).json()['total'] == 0
    assert client.get('/api/products', params=query | {'source': 'GigaB2B'}).json()['total'] == 0
    # Existing multi-word queries remain substring searches.
    assert client.get('/api/products', params={'q': 'Old chair'}).json()['total'] == 3
    exported = client.get('/api/export.csv', params=query)
    assert exported.status_code == 200
    rows = list(csv.reader(io.StringIO(exported.content.decode('utf-8-sig'))))
    assert {row[1] for row in rows[1:]} == {'parent-0', 'parent-1', 'parent-2'}
    assert catalog.find(identifiers='\n ;， ')['total'] == 4


def test_deduplication_and_all_filtered_prelisting_use_same_match_set(library):
    client, catalog = library
    duplicate = product(0, external_id='second-template', template='second', store='MYZ-L-22')
    catalog.ingest(ImportBundle(namespace='batch', label='duplicate', listings=[duplicate]))
    query = {'identifiers': 'child-0;child-1', 'deduplicate': True, 'source': 'Amazon'}
    result = client.get('/api/products', params=query).json()
    assert result['total'] == 2
    plan = client.post('/api/prelisting/preview', json=inputs(client, filters=query))
    assert plan.status_code == 200, plan.text
    assert {item['listing_id'] for item in plan.json()['items']} == {item['id'] for item in result['items']}
    assert {v['sku'] for item in plan.json()['items'] for v in item['variants']} == {'child-0', 'child-1'}
    assert client.get('/api/products', params=query | {'store': 'MYZ-L-22'}).json()['total'] == 1


def test_giga_supplier_skus_and_empty_asins(library):
    client, catalog = library
    items = [product(i + 10, asin='', sku=f'W-SUPPLIER-{i}', source='GigaB2B') for i in range(2)]
    catalog.ingest(ImportBundle(namespace='giga-batch', label='giga', listings=items))
    result = client.get('/api/products', params={
        'identifiers': 'w-supplier-0\nW-SUPPLIER-1', 'source': 'GigaB2B', 'deduplicate': True}).json()
    assert result['total'] == 2
    assert {item['sku'] for item in result['items']} == {'W-SUPPLIER-0', 'W-SUPPLIER-1'}


@pytest.mark.parametrize('identifiers', [','.join(f'SKU-{i}' for i in range(101)), 'x' * 201, 'x' * 6001])
def test_limits_consistent_for_list_export_and_prelisting(library, identifiers):
    client, catalog = library
    for path in ['/api/products', '/api/export.csv']:
        assert client.get(path, params={'identifiers': identifiers}).status_code == 422
    response = client.post('/api/prelisting/preview', json=inputs(client, filters={'identifiers': identifiers}))
    assert response.status_code == 422
    assert catalog.find()['total'] == 4


def test_one_hundred_identifiers_and_duplicate_normalization(library):
    client, _ = library
    query = 'parent-0\nPARENT-0；' + '、'.join(f'SKU-{i}' for i in range(99))
    assert len(parse_identifiers(query)) == 100
    response = client.get('/api/products', params={'identifiers': query})
    assert response.status_code == 200 and response.json()['total'] == 1
