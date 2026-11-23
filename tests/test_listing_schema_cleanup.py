import copy
import json

import pytest

from catalog.listing_schema_cleanup import cleanup
from catalog.listing_prices import suggested_msrp, fill_msrp
from catalog.walmart_listing_schema import sections, precise_validator, chinese_schema_error
from catalog.walmart_publishing import default_rows
from test_walmart_accounts import vault
from test_giga import settings
from test_giga_prelisting import warehouse
from test_walmart_publishing import publishing, ready, PT, spec, send
from test_listing_generation import listing


@pytest.mark.parametrize('price,expected',[
    ('87.47','174.99'),('60.00','120.99'),('79.73','159.99'),
    ('0.01','0.99'),('10.50','21.99'),('invalid',None),(None,None),('NaN',None),('-1',None),
])
def test_editable_msrp_suggestion_decimal_99(price,expected):
    value=suggested_msrp(price)
    assert (str(value) if value is not None else None)==expected


def test_msrp_is_per_child_not_highest_family_price():
    preview=dict(brand='Brand',items=[dict(listing_id='family',msrp='400.99',variants=[
        dict(sku='one',platform_sku='MY21ONE111',price='50.10',content={}),
        dict(sku='two',platform_sku='MY21TWO222',price='200.49',content={})])])
    assert [r['orderable']['msrp'] for r in default_rows(preview)]==[100.99,400.99]
    for value in (0,123.45,999.99):
        orderable=dict(price=50.10,msrp=value)
        assert not fill_msrp(orderable) and orderable['msrp']==value


def test_nightstands_cleanup_uses_selected_schema_and_keeps_nested_valid_fields():
    row=listing();row['product_type']='Nightstands'
    schema=spec()
    visible=schema['properties']['MPItem']['items']['properties']['Visible']
    visible['properties']['Nightstands']=visible['properties'].pop(PT)
    visible['required']=['Nightstands']
    props=sections(schema,'Nightstands')['visible']['properties']
    props['details']={'type':'object','properties':{'material':{'type':'string'}},
                      'patternProperties':{'^supported_':{'type':'string'}},'additionalProperties':False}
    row['visible'].update(oldSofaDesign='Sleeper',details=dict(material='Wood',wrongField='old',supported_note='Keep'))
    row['orderable'].update(price=87.47,msrp=123.45)
    original=copy.deepcopy(row)
    errors=list(precise_validator(sections(schema,'Nightstands')['visible']).iter_errors(row['visible']))
    translated=[chinese_schema_error(error) for error in errors if error.validator=='additionalProperties']
    assert any('oldSofaDesign' in error['message'] and error['path'].endswith('oldSofaDesign') for error in translated)
    assert any('wrongField' in error['message'] and error['path'].endswith('details.wrongField') for error in translated)
    audit=cleanup(row,schema)
    assert row['product_type']=='Nightstands'
    assert 'oldSofaDesign' not in row['visible']
    assert row['visible']['details']==dict(material='Wood',supported_note='Keep')
    assert row['orderable']==original['orderable']
    assert audit['visible.oldSofaDesign']['previous_value']=='Sleeper'
    assert audit['visible.details.wrongField']['previous_value']=='old'
    assert cleanup(row,schema)=={}


def test_cleanup_keeps_invalid_values_and_required_routing_for_real_validation():
    row=listing();schema=spec()
    row['visible'].update(isProp65WarningRequired='Invalid',variantGroupId='saved-group')
    row['orderable'].update(price=87.47,msrp=123.45)
    audit=cleanup(row,schema)
    assert not audit
    assert row['visible']['isProp65WarningRequired']=='Invalid'
    assert row['visible']['variantGroupId']=='saved-group'
    assert any(error.validator=='enum' for error in precise_validator(sections(schema,PT)['visible']).iter_errors(row['visible']))


@pytest.mark.parametrize('endpoint',['prepare','validate'])
def test_existing_draft_removes_stale_category_fields_with_audit(publishing,endpoint):
    client,catalog,_,_,batch,base,remote=publishing
    doc=ready(publishing)
    for row in doc['rows']:
        row['visible']['oldCategoryProperty']='Old value'
        row['orderable']['msrp']=123.45
    saved=client.patch(base,json={k:doc[k] for k in ('revision','version','rows')})
    assert saved.status_code==200,saved.text
    before=saved.json()
    response=client.post(base+'/'+endpoint,json={'revision':before['revision']} if endpoint=='validate' else None)
    assert response.status_code==200,response.text
    updated=response.json()
    assert all('oldCategoryProperty' not in row['visible'] for row in updated['rows'])
    assert all(row['orderable']['msrp']==123.45 for row in updated['rows'])
    assert all(row['product_type']==PT for row in updated['rows'])
    assert [(r['platform_sku'],r['source_sku']) for r in updated['rows']]==[(r['platform_sku'],r['source_sku']) for r in before['rows']]
    if endpoint=='prepare':
        assert client.post(base+'/prepare').json()['revision']==updated['revision']
        updated=client.post(base+'/validate',json={'revision':updated['revision']}).json()
    assert updated['validation']['valid_count']==2,updated['validation']
    assert not remote.posts
    with catalog.connect() as db:
        events=[json.loads(r[0]) for r in db.execute('SELECT details_json FROM walmart_listing_events WHERE batch_id=?',(batch['id'],))]
    changes=[change for event in events for change in event.get('schema_cleanup',[])]
    assert len(changes)==2
    assert all(change['fields']['visible.oldCategoryProperty']['previous_value']=='Old value' for change in changes)


def test_blank_msrp_fills_on_entry_and_manual_edit_survives_validation_and_feed(publishing):
    client,catalog,_,_,batch,base,remote=publishing
    doc=ready(publishing)
    for row in doc['rows']:
        row['orderable'].update(price=87.47)
        row['orderable'].pop('msrp',None)
    # Existing blanks are repaired on entry, including drafts predating this rule.
    with catalog.connect() as db:
        db.execute('UPDATE walmart_listing_documents SET rows_json=? WHERE batch_id=?',(json.dumps(doc['rows']),batch['id']))
    response=client.post(base+'/prepare')
    assert response.status_code==200,response.text
    doc=response.json()
    assert all(row['orderable']['msrp']==174.99 for row in doc['rows'])
    for row,value in zip(doc['rows'],(0,123.45)):row['orderable']['msrp']=value
    saved=client.patch(base,json={k:doc[k] for k in ('revision','version','rows')})
    assert saved.status_code==200,saved.text
    doc=client.post(base+'/prepare').json()
    assert [row['orderable']['msrp'] for row in doc['rows']]==[0,123.45]
    checked=client.post(base+'/validate',json={'revision':doc['revision']})
    assert checked.status_code==200,checked.text
    assert checked.json()['validation']['valid_count']==2,checked.json()['validation']
    attempt=send(publishing,checked.json())
    assert [r['Orderable']['msrp'] for r in remote.feeds[attempt['feed_id']]['MPItem']]==[0,123.45]
