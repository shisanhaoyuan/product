"""Legacy child source dimensions reach drafts and generation without crossing ASINs."""
import copy
import json
from datetime import datetime, timezone

import pytest

from catalog.listing_source_attributes import attributes, contents
from catalog.listing_variants import plan, preserve_manual
from catalog.listing_generation import source_facts, mapped_value, fill_row
from catalog.listing_variant_rules import pack_count
from catalog.models import ImportBundle
from test_listing_variants import family, variant_publishing
from test_listing_generation import AI, listing
from test_walmart_publishing import PT, ready, spec, send
from test_walmart_accounts import vault
from catalog.walmart_listing_schema import sections
from test_variant_content import local
from test_prelisting import inputs


def test_retained_child_dimensions_wrappers_aliases_and_priority():
    child=dict(attributes={'Size':'Large','Color':''},source_facts={
        'raw_attributes':{'Colour Name':{'val':'Brown'},'Number of Items':'2'},
        'copy':{'raw_attributes':{'Color':'Wrong lower priority','Size':'Small','Finish Type':'Matte',
                                  'Number of Shelves':'5','Model Number':'123'}}})
    result=attributes(child)
    assert result=={'Size':'Large','Colour Name':'Brown','Number of Items':'2','Finish Type':'Matte'}
    assert mapped_value('color',{'type':'string'},{'content.attributes':result})[0]=='Brown'
    assert mapped_value('finish',{'type':'string'},{'content.attributes':result})[0]=='Matte'
    # Conflicting synonyms within one evidence layer are still ambiguous.
    assert mapped_value('color',{'type':'string'},{'content.attributes':attributes({
        'attributes':{'Color':'Red','Colour':'Blue'}})})[0] is None


def test_six_variant_old_basis_fills_color_and_pack_difference_without_ai():
    colors=['Dark Coffee','White','Brown','Black','Grey','Brown']
    rows,basis,schemas=family(colors)
    source={'variants':[]}
    for n,(row,v,color) in enumerate(zip(rows,basis['items'][0]['variants'],colors)):
        v['asin']=f'B{n:09d}'
        v['content']['attributes']={'Color':color} if n in (0,2,5) else {}
        source['variants'].append(dict(sku=v['sku'],asin=v['asin'],source_facts={'copy':{'raw_attributes':{
            'Color':color,'Number of Items':'2' if n==2 else '1'}}}))
        row['variant_manual']=True  # Selecting the axis is not reviewing every value.
        row['visible']['variantAttributeNames']=['color']
    untouched=copy.deepcopy(basis)
    group=plan(rows,basis,schemas,sources={'parent':source},apply=True)[0]
    assert not group['errors'],group['errors']
    assert [r['visible']['color'] for r in rows]==['Dark Coffee','White','Brown**2','Black','Grey','Brown']
    assert group['axes']==['color']
    assert any(m['method']=='user_rule_color_pack_quantity' for m in group['rule_mappings'])
    assert basis==untouched  # Derived data never rewrites frozen source evidence.
    before=copy.deepcopy(rows)
    plan(rows,basis,schemas,sources={'parent':source},apply=True)
    assert rows==before


def test_child_match_requires_own_sku_and_asin_and_never_parent_color():
    rows,basis,schemas=family()
    for n,v in enumerate(basis['items'][0]['variants']):
        v['asin']=f'B{n:09d}';v['content']['attributes']={}
    source={'attributes':{'Color':'Do not inherit'},'variants':[
        dict(sku='child-0',asin='B999999999',source_facts={'copy':{'raw_attributes':{'Color':'Wrong ASIN'}}}),
        dict(sku='other-sku',asin='B000000001',source_facts={'copy':{'raw_attributes':{'Color':'Wrong SKU'}}})]}
    plan(rows,basis,schemas,sources={'parent':source},apply=True)
    assert all('color' not in r['visible'] for r in rows)


@pytest.mark.parametrize('attrs,expected',[
    ({'Number of Items':'2'},2),({'Number of Packs':{'val':'2'}},2),({'Size':'2-Pack'},2),
    ({'Size':'Pack of 2'},2),({'Size':'5-Tier (2 Pack)'},2),
    ({'Size':'24 Inch (2 Pack)'},2),({'Size':'2 Pack / Pack of 3'},None),
    ({'Number of Items':'2','Number of Packs':'1'},None),
    ({'stock':'2','Number of Shelves':'2','Model Number':'2'},None),
    ({'Size':'24 Inch'},None),({'Number of Items':True},None),({'Number of Items':'0'},None),
])
def test_only_explicit_nonconflicting_pack_quantities_are_used(attrs,expected):
    assert pack_count(attrs)==expected


def test_quantity_rule_preserves_reviewed_fields_and_restricted_enums():
    rows,basis,schemas=family(('Brown','Brown'))
    for n,v in enumerate(basis['items'][0]['variants']):
        v['content']['attributes']['Number of Items']=str(n+1)
    rows[0]['variant_manual']=True;rows[0]['variant_reviewed_fields']=['color']
    rows[0]['visible']['color']='My custom brown'
    plan(rows,basis,schemas,apply=True)
    assert rows[0]['visible']['color']=='My custom brown'
    rows,basis,schemas=family(('Brown','Brown'))
    for n,v in enumerate(basis['items'][0]['variants']):v['content']['attributes']['Number of Items']=str(n+1)
    sections(schemas[PT],PT)['visible']['properties']['color']={'type':'string','enum':['Brown']}
    group=plan(rows,basis,schemas,apply=True)[0]
    assert group['errors'] and all(r['visible']['color']=='Brown' for r in rows)


def test_generation_reads_retained_own_color_and_attempts_selected_optional_axis():
    row=listing();row['variant_manual']=True
    row['visible']['variantAttributeNames']=['color']
    schema=spec();props=sections(schema,PT)['visible']['properties']
    props['color']={'type':'string'}  # Optional in the class, required by the selected variation axis.
    source={'source':'Amazon','variants':[{'sku':'W1','asin':'B000000001','source_facts':{
        'copy':{'raw_attributes':{'Color':{'value':'Brown'}}}}}]}
    facts=source_facts(source,{},dict(sku='W1',asin='B000000001',content={}))
    generated,_=fill_row(row,schema,facts,AI(),{})
    assert generated['visible']['color']=='Brown'
    # Manual review of another sibling must not restore this unreviewed blank.
    preserve_manual(generated,row)
    assert generated['visible']['color']=='Brown'
    row['variant_reviewed_fields']=['color']
    preserve_manual(generated,row)
    assert 'color' not in generated['visible']


def test_new_preview_includes_retained_child_color(local):
    client,catalog=local
    old=catalog.export()[0]
    data={k:v for k,v in old.items() if k not in ('id','namespace')}
    data['observed_at']=datetime.now(timezone.utc).isoformat()
    child=data['variants'][0]
    child['attributes']={}
    child['source_facts']['copy']={'raw_attributes':{'Color':'Brown','Number of Items':'2'}}
    catalog.ingest(ImportBundle(namespace=old['namespace'],label='source',listings=[data]))
    response=client.post('/api/prelisting/preview',json=inputs(client))
    assert response.status_code==200,response.text
    content=response.json()['items'][0]['variants'][0]['content']
    assert content['attributes']['Color']=='Brown' and content['attributes']['Number of Items']=='2'


def test_existing_document_prepare_repairs_frozen_missing_colors(variant_publishing):
    client,catalog,_,_,batch,base,_=variant_publishing
    doc=ready(variant_publishing)
    with catalog.connect() as db:
        basis=json.loads(db.execute('SELECT basis_json FROM walmart_listing_documents WHERE batch_id=?',(batch['id'],)).fetchone()[0])
        for v in basis['items'][0]['variants']:v['content']['attributes']={}
        for row in doc['rows']:
            row['visible'].pop('color',None)
            row['variant_manual']=True
            row['visible']['variantAttributeNames']=['color']
        db.execute('UPDATE walmart_listing_documents SET rows_json=?,basis_json=? WHERE batch_id=?',
                   (json.dumps(doc['rows']),json.dumps(basis),batch['id']))
    response=client.post(base+'/prepare')
    assert response.status_code==200,response.text
    repaired=response.json()
    assert [r['visible']['color'] for r in repaired['rows']]==['Red','Blue']
    assert not repaired['variant_groups'][0]['errors']
    assert [r['visible']['color'] for r in client.get(base).json()['rows']]==['Red','Blue']


def test_existing_quantity_colors_are_saved_and_reach_feed_without_accumulation(variant_publishing):
    client,catalog,_,_,batch,base,remote=variant_publishing
    doc=ready(variant_publishing)
    with catalog.connect() as db:
        record=db.execute('SELECT basis_json FROM walmart_listing_documents WHERE batch_id=?',(batch['id'],)).fetchone()
        basis=json.loads(record[0])
        lid=basis['items'][0]['listing_id']
        source=json.loads(db.execute('SELECT data_json FROM current_listings WHERE id=?',(lid,)).fetchone()[0])
        for n,(row,v) in enumerate(zip(doc['rows'],basis['items'][0]['variants'])):
            child=next(c for c in source['variants'] if c['sku']==v['sku'])
            child['attributes']={}
            child.setdefault('source_facts',{})['copy']={'raw_attributes':{
                'Color':'Brown' if n==0 else 'brown','Number of Items':str(n+1)}}
    original=catalog.export()[0]
    data={k:v for k,v in source.items() if k not in ('id','namespace')}
    data['observed_at']=datetime.now(timezone.utc).isoformat()
    catalog.ingest(ImportBundle(namespace=original['namespace'],label='updated source',listings=[data]))
    refreshed=client.post(base+'/refresh-source',json={'revision':doc['revision']})
    assert refreshed.status_code==200,refreshed.text
    doc=refreshed.json()
    # Simulate an old draft that froze no colors although its matching source
    # children already retained them in the raw collection facts.
    with catalog.connect() as db:
        basis=json.loads(db.execute('SELECT basis_json FROM walmart_listing_documents WHERE batch_id=?',(batch['id'],)).fetchone()[0])
        for row,v in zip(doc['rows'],basis['items'][0]['variants']):
            v['content']['attributes']={}
            row['visible'].pop('color',None)
            row['variant_manual']=True
            row['visible']['variantAttributeNames']=['color']
        db.execute('UPDATE walmart_listing_documents SET rows_json=?,basis_json=? WHERE batch_id=?',
                   (json.dumps(doc['rows']),json.dumps(basis),batch['id']))
    for _ in range(2):
        response=client.post(base+'/prepare')
        assert response.status_code==200,response.text
        doc=response.json()
        assert [r['visible']['color'] for r in doc['rows']]==['Brown','brown**2']
        assert not doc['variant_groups'][0]['errors']
    checked=client.post(base+'/validate',json={'revision':doc['revision']})
    assert checked.status_code==200,checked.text
    assert checked.json()['validation']['valid_count']==2,json.dumps(checked.json()['validation']['rows'],ensure_ascii=False)
    attempt=send(variant_publishing,checked.json())
    wire=remote.feeds[attempt['feed_id']]['MPItem']
    assert [r['Visible'][PT]['color'] for r in wire]==['Brown','brown**2']
    assert all(r['Visible'][PT]['variantAttributeNames']==['color'] for r in wire)
    assert len({r['Visible'][PT]['variantGroupId'] for r in wire})==1
