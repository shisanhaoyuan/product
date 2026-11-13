import json
from copy import deepcopy

import pytest

from catalog.db import Catalog
from catalog.models import ImportBundle
from catalog.source_sync import resolve_missing_issues
from test_editing import local, detail, edit
from test_source_sync import capture, seed
from test_prelisting import product


@pytest.mark.parametrize('reason', ['seller_not_collected', 'Seller is missing',
    'missing_seller_information', '卖家信息未采集', '缺少卖家信息', ''])
def test_recognizes_historical_missing_reasons(reason):
    p = product().model_dump()
    p['issues'] = [{'field': 'seller', 'reason': reason, 'variant_sku': 'child-0'}]
    original = deepcopy(p['issues'])
    resolve_missing_issues(p)
    assert p['issues'] == []
    assert p['resolved_issues'] == original


def test_capture_clears_matching_missing_reasons_in_detail_and_list(local):
    client, catalog = local
    seed(catalog)
    raw = catalog.export()[0]
    raw['issues'] = [
        {'field': 'seller', 'reason': 'seller_not_collected'},
        {'field': 'seller', 'reason': '卖家信息未采集', 'variant_sku': 'child-0'},
        {'field': 'seller', 'reason': 'missing_seller_information', 'details': {'sku': 'other'}},
        {'field': 'shipping_weight', 'sku': 'other'},
        {'key': 'parent_price_stock'},
    ]
    raw['observed_at'] = '2026-11-15T00:00:00+00:00'
    from catalog.models import Listing
    catalog.ingest(ImportBundle(namespace='test', label='historical errors', listings=[Listing.model_validate(raw)]))
    assert capture(client).status_code == 200
    p = detail(client)
    assert len(p['issues']) == 2
    assert len(p['resolved_issues']) == 3
    assert client.get('/api/products').json()['items'][0]['issue_count'] == 2
    assert edit(client, {'field': 'shipping_weight', 'variant_sku': 'other'}, '5.25').status_code == 200
    assert len(detail(client)['issues']) == 1
    assert detail(client)['issues'][0]['key'] == 'parent_price_stock'
    assert not detail(client)['fill_complete']


def test_resolution_is_identity_specific_and_preserves_mismatches():
    p = product().model_dump()
    p['variants'][0]['shipping_weight'] = '0'
    cases = [
        {'field': 'seller', 'sku': 'missing-sku'},
        {'field': 'seller', 'sku': 'child-0', 'errors': ['missing', 'persisted_value_mismatch']},
        {'field': 'seller', 'details': {'expected': 'Other Seller', 'reason': 'missing'}},
        {'field': 'seller', 'section': 'save', 'reason': 'missing'},
        {'field': 'shipping_weight', 'sku': 'child-0'},
        {'field': 'seller', 'reason': 'unrecognized_audit_failure'},
    ]
    p['issues'] = deepcopy(cases)
    resolve_missing_issues(p)
    assert p['issues'] == cases
    assert not p['resolved_issues']
    # ASIN references are accepted only when unique.
    p['issues'] = [{'field': 'seller', 'sku': p['asin']}]
    p['variants'].append(dict(p['variants'][0], sku='another'))
    resolve_missing_issues(p)
    assert len(p['issues']) == 1


def test_empty_html_does_not_resolve_seller():
    p = product(seller='<p>&nbsp;</p>').model_dump()
    p['issues'] = [{'field': 'seller'}]
    resolve_missing_issues(p)
    assert len(p['issues']) == 1


def test_reopen_rebuilds_old_postgres_projection_without_modifying_evidence(local):
    client, catalog = local
    p = product(fill_complete=False, issues=[{'field': 'seller', 'reason': 'seller_not_collected'}])
    p.observed_at = '2026-11-16T00:00:00+00:00'
    catalog.ingest(ImportBundle(namespace='test', label='already filled', listings=[p]))
    original = catalog.export()
    lid = detail(client)['id']
    with catalog.connect() as db:
        db.execute('DELETE FROM migrations WHERE version=12')
        if catalog.postgres:
            stale = json.loads(db.execute('SELECT data_json FROM current_listings WHERE id=?', (lid,)).fetchone()[0])
            stale['issues'] = p.issues
            db.execute('UPDATE current_listings SET data_json=? WHERE id=?', (json.dumps(stale), lid))
    reopened = Catalog(catalog.path)
    assert reopened.get(lid)['issues'] == []
    assert len(reopened.get(lid)['resolved_issues']) == 1
    assert reopened.find()['items'][0]['issue_count'] == 0
    assert reopened.export() == original
    assert not reopened.get(lid)['fill_complete']
