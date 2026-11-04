import csv
import io

from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.db import Catalog
from catalog.models import ImportBundle, Listing


def item(index, category=None, **changes):
    return Listing.model_validate(dict(
        external_id=f'item-{index}', asin='B012345678', sku=f'sku-{index}',
        store='MYZ-L-21', brand='Growlume', template=f'template-{index}',
        observed_at='2026-11-04T10:00:00+00:00', uploaded_at='2026-11-04',
        evidence_kind='source_only', fields={} if category is None else {'dc_productType': category},
        **changes,
    ))


def test_category_counts_exact_filter_and_missing(tmp_path):
    catalog = Catalog(tmp_path / 'test.db')
    values = [' Cat Trees (猫树)\n', 'Cat Trees (猫树)', 'Cat Trees & Towers', '', None, '请选择', ['invalid'], {'label': 'invalid'}, 4, 'missing', "Baker's Racks (贝克架)"]
    catalog.ingest(ImportBundle(namespace='test', label='categories', listings=[item(i, v) for i, v in enumerate(values)]))
    before = catalog.export()
    categories = catalog.stats()['categories']
    assert sum(c['count'] for c in categories) == len(values)
    assert next(c for c in categories if c['value'] == 'missing')['count'] == 6
    assert next(c for c in categories if c['value'] == 'type:Cat Trees (猫树)')['count'] == 2
    result = catalog.find(category='type:Cat Trees (猫树)', page_size=1)
    assert result['total'] == 2 and len(result['items']) == 1
    assert result['items'][0]['category'] == 'Cat Trees (猫树)'
    assert catalog.find(category='type:Cat Trees (猫树)', page=2, page_size=1)['items'][0]['sku'] != result['items'][0]['sku']
    assert catalog.find(category='missing')['total'] == 6
    assert catalog.find(category='type:missing')['total'] == 1
    assert catalog.find(category="type:Baker's Racks (贝克架)")['total'] == 1
    assert catalog.find(category="type:' OR 1=1--")['total'] == 0
    assert catalog.find(category='obsolete-value')['total'] == 0
    assert catalog.find(category='type:Cat Trees (猫树)', store='missing-store')['total'] == 0
    assert catalog.find(category='type:Cat Trees (猫树)', brand='Growlume', date='2026-11-04', q='sku-0', status='review')['total'] == 1
    assert catalog.get(result['items'][0]['id'])['category'] == 'Cat Trees (猫树)'
    assert catalog.export() == before  # Raw evidence is neither trimmed nor rewritten.


def test_category_import_update_and_csv_api(tmp_path, monkeypatch):
    monkeypatch.delenv('CATALOG_AUTH_ENABLED', raising=False)
    app = create_app(tmp_path / 'test.db')
    client = TestClient(app)
    items = [item(0, 'Cat Trees (猫树)'), item(1, 'Desk Chairs (书桌椅)')]
    bundle = ImportBundle(namespace='test', label='categories', listings=items)
    catalog = app.state.catalog
    catalog.ingest(bundle)
    assert catalog.ingest(bundle)['unchanged'] == 2
    params = {'category': 'type:Cat Trees (猫树)', 'store': 'MYZ-L-21'}
    response = client.get('/api/products', params=params).json()
    assert response['total'] == 1 and response['items'][0]['sku'] == 'sku-0'
    rows = list(csv.DictReader(io.StringIO(client.get('/api/export.csv', params=params).text.lstrip('\ufeff'))))
    assert len(rows) == 1 and rows[0]['商品类目'] == 'Cat Trees (猫树)' and rows[0]['商品SKU'] == 'sku-0'
    changed = items[0].model_copy(deep=True)
    changed.fields['dc_productType'] = 'Desk Chairs (书桌椅)'
    changed.observed_at = '2026-11-04T11:00:00+00:00'
    catalog.ingest(ImportBundle(namespace='test', label='new category', listings=[changed]))
    assert client.get('/api/products', params=params).json()['total'] == 0
    assert client.get('/api/stats').json()['categories'] == [{'value': 'type:Desk Chairs (书桌椅)', 'label': 'Desk Chairs (书桌椅)', 'count': 2}]
    detail = catalog.get(response['items'][0]['id'])
    assert len(detail['history']) == 2
    assert detail['fields']['dc_productType'] == 'Desk Chairs (书桌椅)'
