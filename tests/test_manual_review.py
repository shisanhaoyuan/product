import json
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from catalog.app import create_app
from catalog.models import ImportBundle
from test_editing import local, detail, edit
from test_auth import team, login
from test_prelisting import product


def confirm(client, complete=True, revision=None):
    p = detail(client)
    return client.post(f"/api/products/{p['id']}/review", json={
        'expected_revision': revision or p['local_edit']['revision'], 'complete': complete,
        'note': '已核对价格和库存'})


def test_review_persists_updates_filters_and_preserves_source(local):
    client, catalog = local
    edit(client, {'field': 'price', 'variant_sku': 'child-0'}, '89.99')
    before = detail(client)
    source = catalog.export()
    result = confirm(client)
    assert result.status_code == 200, result.text
    p = result.json()
    assert p['fill_complete'] and p['manual_review']['valid']
    assert not p['save_succeeded'] and not p['readback_verified']
    assert p['copy'] == before['copy'] and p['variants'] == before['variants']
    assert p['issues'] == before['issues']
    assert catalog.export() == source
    assert client.get('/api/products?status=complete').json()['total'] == 1
    assert client.get('/api/products?status=review').json()['total'] == 0
    assert client.get('/api/stats').json()['complete'] == 1
    reopened = TestClient(create_app(catalog.path))
    assert detail(reopened)['manual_review'] == p['manual_review']
    with catalog.connect() as db:
        event = db.execute("SELECT data_json FROM product_edit_events WHERE action='review'").fetchone()
        assert json.loads(event[0])['after'][-1]['value']['note'] == '已核对价格和库存'
    assert confirm(client, revision=before['local_edit']['revision']).status_code == 409
    assert confirm(client, False).json()['fill_complete'] is False
    assert client.get('/api/products?status=review').json()['total'] == 1


def test_new_edits_invalidate_but_noop_and_same_import_preserve_review(local):
    client, catalog = local
    edit(client, {'field': 'seller'}, 'Seller A')
    edit(client, {'field': 'brand'}, 'Brand B')
    confirm(client)
    before = detail(client)
    assert edit(client, {'field': 'brand'}, before['brand']).json()['manual_review']['valid']
    assert edit(client, {'field': 'seller'}, 'Seller A').json()['manual_review']['valid']
    original = product()
    # Re-ingest exact source, not an effective local projection.
    raw = catalog.export()[0]
    original = original.model_validate({k:v for k,v in raw.items() if k not in {'namespace','id'}})
    catalog.ingest(ImportBundle(namespace='test', label='repeat', listings=[original]))
    assert detail(client)['manual_review']['valid']
    p = edit(client, {'field': 'stock', 'variant_sku': 'child-0'}, '0').json()
    assert not p['fill_complete'] and p['manual_review'] is None
    confirm(client)
    newer = original.model_copy(deep=True)
    newer.observed_at = '2026-12-01T00:00:00+00:00'
    newer.fill_complete = False
    catalog.ingest(ImportBundle(namespace='test', label='new', listings=[newer]))
    p = detail(client)
    assert not p['fill_complete'] and not p['manual_review']['valid']


def test_concurrent_review_and_edit_reject_stale_write(local):
    client, _ = local
    version = detail(client)['local_edit']['revision']
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(confirm, client, True, version)
        b = pool.submit(edit, client, {'field':'brand'}, 'New brand', version)
        assert sorted([a.result().status_code, b.result().status_code]) == [200,409]


def test_review_roles_and_confirm_without_erp_evidence(team):
    item = product()
    item.evidence_kind='source_only'
    item.save_succeeded=item.readback_verified=item.fill_complete=False
    team.state.catalog.ingest(ImportBundle(namespace='test',label='test',listings=[item]))
    viewer, _ = login(team, 'viewer')
    assert confirm(viewer).status_code == 403
    editor, _ = login(team, 'editor')
    p = confirm(editor).json()
    assert p['fill_complete'] and p['manual_review']['actor']=='editor'
    assert not p['save_succeeded'] and not p['readback_verified']
    assert p['evidence_kind']=='source_only'
    assert editor.patch(f"/api/products/{p['id']}/edits", json={
        'expected_revision':p['local_edit']['revision'], 'field':'_manual_review','value':{}}).status_code==422
