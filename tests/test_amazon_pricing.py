import json
from decimal import Decimal

import pytest
from catalog.amazon_pricing import price_plan
from test_prelisting import local, inputs, product, create


def capture(db, lid, sku, asin, original='72.89', **extra):
    data = dict(asin=asin, currency='USD', original_price=original, price='63.99',
                shipping='8.00', delivery_location='New York 10001') | extra
    db.execute('INSERT INTO source_captures VALUES(?,?,?,?,?,?,?)',
               ('test-capture', lid, sku, asin, '2026-11-20T10:00:00Z', None, json.dumps(data)))


def test_latest_capture_original_overrides_erp_and_old_facts(local):
    client, catalog = local
    with catalog.connect() as db:
        row = db.execute("SELECT id,data_json FROM listings WHERE external_id='item-0'").fetchone()
        p=json.loads(row['data_json']);v=p['variants'][0]
        capture(db,row['id'],v['sku'],v['asin'])
        result=price_plan(db,row['id'],p,v,Decimal('1.4'))
        assert result['base']==result['original']=='72.89'
        assert result['price']=='102.05' and result['landed']=='71.99'
        assert not result['capture_required']
    preview=client.post('/api/prelisting/preview',json=inputs(client,multiplier='1.4')).json()
    item=next(i for i in preview['items'] if i['sku']=='parent-0')
    assert item['variants'][0]['price']=='102.05'


@pytest.mark.parametrize('changes', [dict(original_price=None),dict(original_price='0'),
    dict(original_price='NaN'),dict(currency='EUR'),dict(delivery_location='Seattle 98101')])
def test_bad_capture_never_falls_back_to_erp_or_older_facts(local,changes):
    _,catalog=local
    with catalog.connect() as db:
        row=db.execute("SELECT id,data_json FROM listings WHERE external_id='item-0'").fetchone()
        p=json.loads(row['data_json']);v=p['variants'][0]
        capture(db,row['id'],v['sku'],v['asin'],**changes)
        result=price_plan(db,row['id'],p,v,Decimal('1.4'))
        assert result['price'] is None and result['capture_required'] and result['error']


def test_parent_capture_is_shared_only_with_unique_same_asin_child(local):
    _,catalog=local
    with catalog.connect() as db:
        row=db.execute("SELECT id,data_json FROM listings WHERE external_id='item-0'").fetchone()
        p=json.loads(row['data_json']);v=p['variants'][0];v['source_facts']={}
        capture(db,row['id'],'',p['asin'])
        assert price_plan(db,row['id'],p,v,Decimal('1.4'))['price']=='102.05'
        other=v | {'sku':'other','asin':'B999999999'}
        assert price_plan(db,row['id'],p,other,Decimal('1.4'))['price'] is None
        p['variants'].append(v | {'sku':'duplicate'})
        assert price_plan(db,row['id'],p,v,Decimal('1.4'))['price'] is None


def test_missing_price_requests_capture_and_single_uses_parent_scope(local):
    _,catalog=local
    with catalog.connect() as db:
        row=db.execute("SELECT id,data_json FROM listings WHERE external_id='item-0'").fetchone()
        p=json.loads(row['data_json']);p['variants']=[];p['source_facts']={}
        result=price_plan(db,row['id'],p,p,Decimal('1.4'))
        assert result['capture_required'] and result['capture_sku']=='' and result['price'] is None


def test_auto_capture_overlay_does_not_invalidate_previously_complete_listing(local):
    from catalog.source_sync import sync_capture
    client,catalog=local
    with catalog.connect() as db:
        row=db.execute("SELECT id,data_json FROM listings WHERE external_id='item-0'").fetchone()
        p=json.loads(row['data_json']);v=p['variants'][0]
        result=dict(asin=v['asin'],seller='Seller A',original_price='72.89',price='63.99',shipping='0.00',
                    currency='USD',delivery_location='New York 10001',delivery='Delivery tomorrow',captured_at='2026-11-20T10:00:00Z')
        capture(db,row['id'],v['sku'],v['asin'])
        sync_capture(catalog,db,row['id'],v['sku'],v['asin'],result,None)
    preview=client.post('/api/prelisting/preview',json=inputs(client,multiplier='1.4')).json()
    item=next(i for i in preview['items'] if i['sku']=='parent-0')
    assert item['status']=='ready', item['errors']
    assert item['variants'][0]['price']=='102.05'


@pytest.mark.parametrize('current', ['85.49', None])
def test_original_is_multiplier_basis_even_with_discount_or_ambiguous_current(local, current):
    _, catalog = local
    with catalog.connect() as db:
        row = db.execute("SELECT id,data_json FROM listings WHERE external_id='item-0'").fetchone()
        p = json.loads(row['data_json']); v = p['variants'][0]
        capture(db, row['id'], v['sku'], v['asin'], original='89.99', price=current, shipping='0.00')
        result = price_plan(db, row['id'], p, v, Decimal('1.4'))
        assert result['base'] == result['original'] == '89.99'
        assert result['price'] == '125.99'
        assert result['source_price'] == current
        assert result['landed'] == ('85.49' if current else None)
        assert not result['capture_required']


@pytest.mark.parametrize('state,scope,expected', [
    ('not_displayed','main_offer','201.59'),
    ('not_displayed','sellers_on_amazon_list:1','201.59'),
    ('ambiguous','main_offer',None),
    ('not_displayed','',None),
])
def test_ordinary_price_only_when_reference_absent_and_offer_verified(local,state,scope,expected):
    _,catalog=local
    with catalog.connect() as db:
        row=db.execute("SELECT id,data_json FROM listings WHERE external_id='item-0'").fetchone()
        p=json.loads(row['data_json']);v=p['variants'][0]
        capture(db,row['id'],v['sku'],v['asin'],original=None,price='143.99',
                status='captured',original_price_state=state,offer_scope=scope)
        result=price_plan(db,row['id'],p,v,Decimal('1.4'))
        assert result['price']==expected
        assert result['original'] is None
        if expected:
            assert result['price_basis']=='amazon_regular_no_reference_v1'
            assert result['base']=='143.99'
        else:
            assert result['capture_required']
