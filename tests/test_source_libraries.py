from fastapi.testclient import TestClient
from catalog.app import create_app
from catalog.models import ImportBundle
from catalog.giga import listing_from_api
from catalog.prelisting import Filters
from test_catalog import listing


def libraries(tmp_path):
    app = create_app(tmp_path/'libraries.db')
    catalog = app.state.catalog
    amazon = listing().model_copy(update={'brand':'Amazon brand', 'fields':{'dc_productType':'Amazon category'}})
    giga = listing_from_api('W100', {'sku':'W100','productName':'Cloud chair','category':'Cloud category'},
                            {'sku':'W100','price':'43.99','currency':'CAD'},
                            {'sku':'W100','sellerInventoryInfo':{'sellerAvailableInventory':5}},
                            'giga.test', '2026-11-13T00:00:00+00:00')
    giga.brand = 'Cloud brand'
    for namespace, item in [('amazon.test',amazon),('giga.test',giga)]:
        catalog.ingest(ImportBundle(namespace=namespace,label='source test',listings=[item]))
    return catalog, TestClient(app)


def test_source_lists_stats_filters_exports(tmp_path):
    catalog, client = libraries(tmp_path)
    for source, sku, brand, category, variants in [
        ('Amazon','B012345678-p','Amazon brand','Amazon category',1),
        ('GigaB2B','W100','Cloud brand','Cloud category',0),
    ]:
        for dedup in (False,True):
            rows=client.get('/api/products',params={'source':source,'deduplicate':str(dedup).lower()}).json()
            assert rows['total'] == 1 and rows['items'][0]['sku'] == sku
        stats=client.get('/api/stats',params={'source':source}).json()
        assert stats['listings'] == stats['products'] == 1
        assert stats['variants'] == variants
        assert stats['brands'] == [brand]
        assert stats['categories'] == [{'value':'type:'+category,'label':category,'count':1}]
        assert len(stats['dates']) == len(stats['stores']) == 1
        assert stats['unique_counts']['all_products'] == 1
        csv=client.get('/api/export.csv',params={'source':source}).text
        assert sku in csv
        assert ('W100' if source == 'Amazon' else 'B012345678-p') not in csv
    assert catalog.stats()['products'] == 2  # legacy aggregate API remains available
    assert client.get('/api/products',params={'source':'Amazon','brand':'Cloud brand'}).json()['total'] == 0
    assert client.get('/api/products',params={'source':'GigaB2B','q':'B012345678'}).json()['total'] == 0
    assert client.get('/api/products',params={'source':'invalid'}).status_code == 422
    assert client.get('/api/stats',params={'source':'invalid'}).status_code == 422


def test_cross_page_prelisting_selection_stays_in_source(tmp_path):
    catalog, _=libraries(tmp_path)
    filters=Filters(source='Amazon',deduplicate=True)
    query,args=catalog.selection_sql(**filters.model_dump())
    with catalog.connect() as db:
        rows=db.execute(query,args).fetchall()
    assert len(rows)==1 and rows[0]['sku']=='B012345678-p'
    assert catalog.stats(source='Other')['listings']==0
