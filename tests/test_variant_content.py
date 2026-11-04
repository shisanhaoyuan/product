import json

from fastapi.testclient import TestClient
from catalog.app import create_app
from catalog.models import Variant
from test_editing import local, detail, edit
from test_prelisting import inputs, create


def test_legacy_cells_and_explicit_fields():
    raw = {'sku': 'sku-a', 'raw_cells': {
        '6': {'value': {'product id type': 'EAN'}},
        '8': {'value': {'sva': 'color', 'siu': {'photo1': 'https://example.com/swatch.png'}}},
        '13': {'value': {'shippingWeight': '12.50'}},
    }}
    v = Variant.model_validate(raw)
    assert (v.product_id_type, v.swatch_attribute, v.swatch_image, v.shipping_weight) == (
        'EAN', 'color', 'https://example.com/swatch.png', '12.50')
    assert Variant.model_validate(raw | {'swatch_image': ''}).swatch_image == ''
    assert Variant(sku='no-evidence').product_id_type == ''


def test_child_new_fields_persistence_and_isolation(local):
    client, catalog = local
    before = catalog.export()
    other = detail(client)['variants'][1]
    changes = {'product_id_type': 'EAN', 'swatch_attribute': 'color',
               'swatch_image': 'https://example.com/swatch.png', 'shipping_weight': '6.75'}
    for field, value in changes.items():
        response = edit(client, {'field': field, 'variant_sku': 'child-0'}, value)
        assert response.status_code == 200, response.text
    reopened = detail(TestClient(create_app(catalog.path)))
    assert all(reopened['variants'][0][k] == v for k, v in changes.items())
    assert reopened['variants'][1] == other
    assert catalog.export() == before
    for field, value in [('swatch_image', 'javascript:alert(1)'), ('product_id_type', 'made-up'),
                          ('shipping_weight', '-1'), ('shipping_weight', 'NaN')]:
        assert edit(client, {'field': field, 'variant_sku': 'child-0'}, value).status_code == 422


def test_selected_variant_draft_freezes_full_edited_content(local):
    client, catalog = local
    p = detail(client)
    assert edit(client, {'field': 'copy.title', 'variant_sku': 'child-0'}, 'Edited selected title').status_code == 200
    assert edit(client, {'field': 'swatch_image', 'variant_sku': 'child-0'}, 'https://example.com/a.png').status_code == 200
    settings = inputs(client)
    settings.pop('filters')
    settings.update(listing_ids=[p['id']], variant_skus={p['id']: ['child-0']})
    batch, payload = create(client, settings)
    assert batch['preview']['variant_count'] == 1
    v = batch['preview']['items'][0]['variants'][0]
    assert v['sku'] == 'child-0' and v['content']['copy']['title'] == 'Edited selected title'
    assert v['content']['swatch_image'] == 'https://example.com/a.png'
    assert batch['execution_enabled'] is False
    assert client.post('/api/prelisting/batches', json=payload).json()['id'] == batch['id']
    assert client.post(f"/api/prelisting/batches/{batch['id']}/action", json={'action': 'confirm'}).status_code == 409
    edit(client, {'field': 'copy.title', 'variant_sku': 'child-0'}, 'Later title')
    frozen = client.get('/api/prelisting/batches/' + batch['id']).json()
    assert frozen['preview']['items'][0]['variants'][0]['content']['copy']['title'] == 'Edited selected title'
    with catalog.connect() as db:
        stored = json.loads(db.execute('SELECT settings_json FROM prelisting_batches WHERE id=?', (batch['id'],)).fetchone()[0])
    assert stored['variant_skus'] == settings['variant_skus']


def test_variant_selection_rejects_cross_product_and_duplicates(local):
    client, _ = local
    p = detail(client)
    settings = inputs(client)
    settings.pop('filters')
    settings['listing_ids'] = [p['id']]
    for selection, code in [({p['id']: ['unknown']}, 409), ({p['id']: ['child-0', 'child-0']}, 422),
                            ({'other-product': ['child-0']}, 422), ({p['id']: []}, 422)]:
        assert client.post('/api/prelisting/preview', json=settings | {'variant_skus': selection}).status_code == code
    batch, _ = create(client, settings | {'variant_skus': {p['id']: ['second-child']}})
    # Recompute the exact selected scope on confirmation, rather than restoring all siblings.
    assert client.post(f"/api/prelisting/batches/{batch['id']}/action", json={'action': 'confirm'}).status_code == 409  # mismatching SKU price audit
    assert batch['preview']['variant_count'] == 1


def test_confirm_retains_selected_scope(local):
    client, _ = local
    p = detail(client)
    settings = inputs(client)
    settings.pop('filters')
    settings.update(listing_ids=[p['id']], variant_skus={p['id']: ['child-0']})
    batch, _ = create(client, settings)
    response = client.post(f"/api/prelisting/batches/{batch['id']}/action", json={'action': 'confirm'})
    assert response.status_code == 200, response.text
    assert response.json()['preview']['variant_count'] == 1
    assert response.json()['execution_enabled'] is False
