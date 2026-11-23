from catalog.models import ImportBundle
from test_prelisting import local, product


def test_product_pages_up_to_500_keep_counts_filters_and_unique_items(local):
    client,catalog=local
    catalog.ingest(ImportBundle(namespace='large-page',label='pagination',listings=[
        product(i+1000,store='pagination') for i in range(503)]))
    params=dict(store='pagination',source='Amazon',deduplicate='true')
    default=client.get('/api/products',params=params).json()
    assert default['page_size']==50 and len(default['items'])==50 and default['total']==503
    for size in range(50,501,50):
        response=client.get('/api/products',params=params|{'page_size':size})
        assert response.status_code==200,response.text
        page=response.json()
        assert len(page['items'])==size and page['total']==503 and page['page_size']==size
    second=client.get('/api/products',params=params|{'page_size':500,'page':2}).json()
    first=client.get('/api/products',params=params|{'page_size':500}).json()
    assert second['total']==503 and len(second['items'])==3
    assert not {row['id'] for row in first['items']} & {row['id'] for row in second['items']}
    for invalid in (0,501,1000):
        assert client.get('/api/products',params=params|{'page_size':invalid}).status_code==422
    for source in ('GigaB2B','GigaB2B_CA','Saleyee'):
        assert client.get('/api/products',params={'source':source,'page_size':500}).status_code==200
