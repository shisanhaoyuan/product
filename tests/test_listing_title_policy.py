"""Explicit title branding and relaxed listing checks retain identity protections."""
import copy
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from catalog.listing_titles import branded_title, source_brands, normalize_copy_brand, brand_errors
from catalog.prelisting import encoded
from catalog.walmart_publishing import source_issues
from catalog.listing_generation import prepare_copy
from test_giga import settings
from test_giga_prelisting import warehouse
from test_walmart_accounts import vault
from test_walmart_publishing import publishing, ready, send, submission


@pytest.mark.parametrize('title,brand,old,expected',[
    ('OldBrand Oak Chair 24.5 Inch', 'Growlume', ['OldBrand'], 'Growlume Oak Chair 24.5 Inch'),
    ('growlume Chair OldBrand GROWLUME', 'Growlume', ['OldBrand'], 'Growlume Chair'),
    ('Old Brand - Chair', 'Growlume', ['Old Brand'], 'Growlume Chair'),
    ('<b>OLD BRAND®</b> Chair &amp; Table', 'Growlume', ['Old Brand'], 'Growlume Chair & Table'),
    ('Oakwood 4-Pack Chair', 'NeoNiches', ['Oak'], 'NeoNiches Oakwood 4-Pack Chair'),
    ('UnknownWord Chair', 'Gaoyuyu', [], 'Gaoyuyu UnknownWord Chair'),
    ('旧牌实木椅', '新牌', ['旧牌'], '新牌 实木椅'),
    ('', 'Growlume', ['OldBrand'], ''),
])
def test_brand_prefix_whole_words_preserves_dimensions_and_is_idempotent(title,brand,old,expected):
    assert branded_title(title,brand,old)==expected
    assert branded_title(expected,brand,old)==expected


def test_manual_title_brand_exception_preserves_other_copy():
    row=dict(visible=dict(productName='Manual OLD chair 24.5 Inch',brand='New',shortDescription='Manual description',keyFeatures=['Manual feature']))
    original=copy.deepcopy(row);audit={}
    prepare_copy(row,{'content.copy':{'title':'Source title'},'source_brand':'OLD'},audit)
    assert row['visible']['productName']=='New Manual chair 24.5 Inch'
    assert row['visible']['shortDescription']==original['visible']['shortDescription']
    assert row['visible']['keyFeatures']==original['visible']['keyFeatures']
    assert audit['visible.productName']['kind']=='user_rule'


def test_old_source_age_does_not_block_validation_and_non_double_msrp_is_preserved(publishing):
    c,catalog,_,_,b,base,remote=publishing
    before=catalog.export();doc=ready(publishing)
    old=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
    with catalog.connect() as db:
        basis=json.loads(db.execute('SELECT basis_json FROM walmart_listing_documents WHERE batch_id=?',(b['id'],)).fetchone()[0])
        for item in basis['items']:item['observed_at']=old
        db.execute('UPDATE walmart_listing_documents SET basis_json=? WHERE batch_id=?',(encoded(basis),b['id']))
    for row in doc['rows']:row['orderable']['msrp']=99.99
    saved=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).json()
    checked=c.post(base+'/validate',json={'revision':saved['revision']}).json()
    assert checked['validation']['valid_count']==2,checked['validation']
    response=c.post(base+'/refresh-source',json={'revision':checked['revision']})
    assert response.status_code==200,response.text
    refreshed=response.json()
    assert all(r['orderable']['msrp']==99.99 for r in refreshed['rows'])
    checked=c.post(base+'/validate',json={'revision':refreshed['revision']}).json()
    with catalog.connect() as db:
        report=json.loads(db.execute('SELECT validation_json FROM walmart_listing_documents WHERE batch_id=?',(b['id'],)).fetchone()[0])
        report['at']=int(time.time())-7200
        db.execute('UPDATE walmart_listing_documents SET validation_json=? WHERE batch_id=?',(encoded(report),b['id']))
    assert c.get(base).json()['validation']['current']
    send(publishing,checked)
    assert all(row['Orderable']['msrp']==99.99 for row in remote.feeds['feed-1']['MPItem'])
    assert catalog.export()==before


def test_age_removed_but_missing_price_stock_and_invalid_timestamp_still_fail():
    item=dict(errors=[],observed_at=(datetime.now(timezone.utc)-timedelta(days=365)).isoformat())
    assert source_issues(item,dict(price='10',stock='10'))==[]
    assert source_issues(item,dict(price=None,stock='0'))
    assert source_issues(item|{'observed_at':'invalid'},dict(price='10',stock='10'))


def test_existing_draft_validate_normalizes_title_and_records_audit_only_once(publishing):
    c,catalog,_,_,b,base,_=publishing
    doc=ready(publishing);before=catalog.export()
    with catalog.connect() as db:
        rows=copy.deepcopy(doc['rows']);rows[0]['visible']['productName']='NewBrand NewBrand Chair W1 24.5 Inch'
        db.execute('UPDATE walmart_listing_documents SET rows_json=? WHERE batch_id=?',(encoded(rows),b['id']))
    checked=c.post(base+'/validate',json={'revision':doc['revision']}).json()
    assert checked['rows'][0]['visible']['productName']=='NewBrand Chair W1 24.5 Inch'
    assert checked['revision']==doc['revision']+1
    assert checked['validation']['revision']==checked['revision'] and checked['validation']['current']
    again=c.post(base+'/validate',json={'revision':checked['revision']}).json()
    assert again['revision']==checked['revision']
    for old,new in zip(rows,again['rows']):
        assert (old['platform_sku'],old['source_sku'],old['listing_id'],old['orderable'],old['identification'])==(new['platform_sku'],new['source_sku'],new['listing_id'],new['orderable'],new['identification'])
    assert catalog.export()==before
    with catalog.connect() as db:
        assert db.execute("SELECT count(*) FROM walmart_listing_events WHERE batch_id=? AND action='title_brand_rule'",(b['id'],)).fetchone()[0]==1
    a=send(publishing,again)
    locked=c.get(base).json();locked['rows'][0]['visible']['productName']='Changed published title'
    assert c.patch(base,json={k:locked[k] for k in ('revision','version','rows')}).status_code==409


def test_edit_save_is_idempotent_and_does_not_reprice(publishing):
    c,catalog,_,_,b,base,_=publishing
    doc=ready(publishing)
    doc['rows'][0]['visible']['productName']='Chair NewBrand'
    doc['rows'][0]['orderable']['msrp']=123.45
    saved=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')}).json()
    assert saved['rows'][0]['visible']['productName']=='NewBrand Chair'
    assert saved['rows'][0]['orderable']['msrp']==123.45
    again=c.patch(base,json={k:saved[k] for k in ('revision','version','rows')}).json()
    assert again['revision']==saved['revision'] and again['rows']==saved['rows']


@pytest.mark.parametrize('source', [
    {'source':'Amazon_CA','brand':'','fields':{'Brand':'Tangkula'}},
    {'source':'Amazon','variants':[{'attributes':{'Brand':{'val':['Tangkula']}}}]},
    {'source_facts':{'listing_facts':{'content.attributes':{'Brand Name':{'value':'Tangkula'}}}}},
    {'source_facts':{'giga':{'detail':{'brandInfo':[{'brandName':'Tangkula'}]}}}},
    {'fields':{'attributes':[{'name':'Brand','value':'Tangkula'}]}},
])
def test_extract_explicit_source_brand_from_all_import_shapes(source):
    before=copy.deepcopy(source)
    assert source_brands(source)==['Tangkula']
    assert source==before
    assert source_brands({'seller':'Other','copy':{'title':'Guess Me Chair'},'manufacturer':'Factory'})==[]


def test_brand_rule_covers_title_descriptions_and_features_without_changing_dimensions():
    visible={'brand':'Myxchome','productName':'Myxchome Tangkula 51.5 Inch Bookcase',
             'shortDescription':'<p>Brand: Tangkula</p><p>Width: 51.5 inches</p>',
             'longDescription':'TANGKULA storage', 'keyFeatures':['Tangkula 10-cube shelves']}
    normalize_copy_brand(visible,'Myxchome',['Tangkula'])
    assert visible['productName']=='Myxchome 51.5 Inch Bookcase'
    assert visible['shortDescription']=='<p>Brand: Myxchome</p><p>Width: 51.5 inches</p>'
    assert visible['longDescription']=='Myxchome storage'
    assert visible['keyFeatures']==['Myxchome 10-cube shelves']
    assert not brand_errors(visible,'Myxchome',['Tangkula'])
    before=copy.deepcopy(visible)
    normalize_copy_brand(visible,'Myxchome',['Tangkula'])
    assert visible==before
    visible['keyFeatures']=['Tang<b>kula</b> shelves']
    assert brand_errors(visible,'Myxchome',['Tangkula'])[0]['path']=='visible.keyFeatures.0'


def test_brand_rule_does_not_corrupt_target_containing_source_token():
    from catalog.listing_templates import replace_brand
    assert replace_brand('Oak Home table; Oakwood finish; Oak stool',['Oak'],'Oak Home')=='Oak Home table; Oakwood finish; Oak Home stool'
    assert not brand_errors({'brand':'Oak Home','productName':'Oak Home Table',
                             'shortDescription':'Oak Home table'},'Oak Home',['Oak'])


def test_submit_rechecks_brands_even_with_existing_valid_report(publishing):
    c,catalog,_,_,b,base,remote=publishing
    doc=ready(publishing)
    with catalog.connect() as db:
        record=db.execute('SELECT data_json FROM listings WHERE id=?',(doc['rows'][0]['listing_id'],)).fetchone()
        source=json.loads(record[0]);source['fields']['Brand']='Tangkula'
        db.execute('UPDATE listings SET data_json=? WHERE id=?',(encoded(source),doc['rows'][0]['listing_id']))
    refreshed=c.post(base+'/refresh-source',json={'revision':doc['revision']})
    assert refreshed.status_code==200,refreshed.text
    checked=c.post(base+'/validate',json={'revision':refreshed.json()['revision']})
    assert checked.status_code==200,checked.text
    doc=checked.json()
    # An old validation stamp cannot authorize a subsequently corrupted row.
    with catalog.connect() as db:
        rows=copy.deepcopy(doc['rows'])
        rows[0]['visible']['shortDescription']='Brand: Tang<b>kula</b>'
        db.execute('UPDATE walmart_listing_documents SET rows_json=? WHERE batch_id=?',(encoded(rows),b['id']))
    response=c.post(base+'/submit',json=submission(doc))
    assert response.status_code==409,response.text
    assert 'Tangkula' in response.text and 'shortDescription' in response.text
    assert not remote.posts
    doc['rows'][0]['visible'].update(productName='NewBrand Tangkula Chair',
        shortDescription='<p>Brand: Tangkula</p><p>Wood chair.</p>', keyFeatures=['Tangkula shelf'])
    saved=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')})
    assert saved.status_code==200,saved.text
    assert 'Tangkula' not in json.dumps(saved.json()['rows'][0]['visible'])
    assert saved.json()['rows'][0]['visible']['productName']=='NewBrand Chair'


def test_brand_cleanup_runs_after_final_ai_copy_step(monkeypatch):
    from catalog.listing_generation import fill_row
    from test_listing_generation import listing, AI, spec
    def final_copy(row,*args,**kwargs):
        row['visible'].update(productName='Tangkula Chair',shortDescription='<p>Brand: Tangkula</p>',
                              keyFeatures=['Tangkula wooden shelf'])
        return []
    monkeypatch.setattr('catalog.listing_copy_generation.fill_missing_copy',final_copy)
    facts={'content.attributes':{'Brand':{'val':['Tangkula']}}}
    row,_=fill_row(listing(),spec(),facts,AI(),{})
    assert row['visible']['productName']=='NewBrand Chair'
    assert 'Tangkula' not in json.dumps(row['visible'])


def test_legacy_brand_validation_requires_recheck(publishing):
    c,catalog,_,_,b,base,remote=publishing
    doc=ready(publishing)
    with catalog.connect() as db:
        report=json.loads(db.execute('SELECT validation_json FROM walmart_listing_documents WHERE batch_id=?',(b['id'],)).fetchone()[0])
        report['title_policy']='brand_prefix_v1'
        db.execute('UPDATE walmart_listing_documents SET validation_json=? WHERE batch_id=?',(encoded(report),b['id']))
    assert not c.get(base).json()['validation']['current']
    assert c.post(base+'/submit',json=submission(doc)).status_code==409
    assert not remote.posts
