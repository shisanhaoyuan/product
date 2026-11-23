import json

import httpx

from catalog.models import ImportBundle
from catalog.walmart_publishing import source_issues
from catalog.walmart_publishing_client import WalmartPublishingClient
from test_prelisting import local, product, inputs, create
from test_walmart_accounts import vault, Remote, bind
from test_walmart_publishing import PublisherRemote, ready, send


def test_imported_incomplete_flag_does_not_block_complete_walmart_draft(local,vault):
    client,catalog=local
    imported=product(85,brand='NewBrand',fill_complete=False)
    child=imported.variants[0]
    child.ean='012345678905'
    child.shipping_weight='10'
    child.images=['https://example.com/child.jpg']
    child.copy.title='NewBrand wood chair'
    child.copy.description='Wood chair for home'
    child.copy.features=['Wood seat']
    catalog.ingest(ImportBundle(namespace='incomplete-import',label='source',listings=[imported]))
    batch,_=create(client,inputs(client,filters={'q':imported.asin}))
    assert batch['preview']['items'][0]['errors']==['商品填写尚未核验完整']
    before=catalog.export()
    Remote().install(client.app)
    bind(client,batch['preview']['store']['id'])
    remote=PublisherRemote()
    client.app.state.walmart_publisher=WalmartPublishingClient(httpx.MockTransport(remote.handler))
    base=f"/api/prelisting/batches/{batch['id']}/walmart"
    assert client.post(base+'/prepare').status_code==200
    env=(client,catalog,None,None,batch,base,remote)
    doc=ready(env)
    assert doc['validation']['valid_count']==1,doc['validation']
    assert not remote.posts
    # The source's historical status stays intact; actual missing draft fields
    # still block publication and get specific, navigable errors.
    doc['rows'][0]['visible'].pop('assembledProductHeight')
    response=client.patch(base,json={k:doc[k] for k in ('revision','version','rows')})
    assert response.status_code==200,response.text
    invalid=client.post(base+'/validate',json={'revision':response.json()['revision']}).json()
    assert invalid['validation']['valid_count']==0
    errors=next(iter(invalid['validation']['rows'].values()))['errors']
    assert any(e['path'].endswith('assembledProductHeight') for e in errors)
    assert all(e['message']!='商品填写尚未核验完整' for e in errors)
    doc=ready(env)
    attempt=send(env,doc)
    assert remote.feeds[attempt['feed_id']]['MPItem'][0]['Orderable']['sku']==doc['rows'][0]['platform_sku']
    assert catalog.export()==before
    with catalog.connect() as db:
        basis=json.loads(db.execute('SELECT basis_json FROM walmart_listing_documents WHERE batch_id=?',(batch['id'],)).fetchone()[0])
    assert '商品填写尚未核验完整' in basis['items'][0]['errors']


def test_legacy_completion_flag_does_not_waive_other_source_failures():
    item=dict(errors=['商品填写尚未核验完整','来源 ASIN 与价格证据不一致','卖家已拉黑：test'],
              observed_at='2026-11-01T00:00:00Z')
    errors=source_issues(item,dict(sku='one',price=None,stock='0'))
    assert '商品填写尚未核验完整' not in errors
    assert '来源 ASIN 与价格证据不一致' in errors
    assert '卖家已拉黑：test' in errors
    assert any('售价' in e for e in errors) and any('库存' in e for e in errors)
