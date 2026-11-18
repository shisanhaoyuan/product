import copy
import json

from catalog.listing_content import normalize, sentences, text, image_urls
from catalog.listing_vision import select_listing_images
from test_giga import settings
from test_giga_prelisting import warehouse
from test_walmart_accounts import vault
from test_walmart_publishing import publishing, ready, send


def test_copy_separates_complete_facts_and_preserves_decimals_units_and_idempotence():
    visible=dict(productName='Target chair',keyFeatures=['Comfort: Soft fabric. Seats two people.', 'Soft fabric.'],
        shortDescription='<p>1. Comfort: Soft fabric. Seats two people. 2. Width is 24.5 in. and weight is 3.25 lb. 3. Blue finish.</p>',
        longDescription='Blue finish. Indoor use.',mainImageUrl='hero',productSecondaryImageURL=['hero','side','side'])
    normalize(visible)
    assert visible['keyFeatures']==['Comfort: Soft fabric. Seats two people.']
    assert visible['shortDescription']=='<p><strong>Features:</strong></p>\n<p>1. Width is 24.5 in. and weight is 3.25 lb.</p>\n<p>2. Blue finish.</p>'
    assert 'Indoor use.' in visible['longDescription'] and 'Blue finish.' not in visible['longDescription']
    assert image_urls(visible)==['hero','side']
    again=copy.deepcopy(visible);normalize(visible)
    assert visible==again
    assert sentences('U.S. design. Size 2.5 ft. wide. 中文。下一句。')==['U.S. design.', 'Size 2.5 ft. wide.', '中文。', '下一句。']


def test_redundant_description_is_not_filled_with_title_or_invented_content():
    visible=dict(productName='New title',keyFeatures=['Soft fabric.'],shortDescription='<p>SOFT FABRIC!</p>')
    normalize(visible)
    assert visible['shortDescription']==''
    assert visible['keyFeatures']==['Soft fabric.']


def test_image_selection_prefers_white_hero_and_parameter_photo_from_full_gallery():
    urls=[f'https://example.com/{i}.jpg' for i in range(25)]
    digests={u:str(i) for i,u in enumerate(urls)}
    # Identical source images are deduplicated by bytes too.
    digests[urls[3]]=digests[urls[2]]
    row=dict(visible=dict(mainImageUrl=urls[0],productSecondaryImageURL=urls[1:8]))
    original=copy.deepcopy(urls)
    report=select_listing_images(row,urls,digests,[
        dict(attribute='listingImageRole',value='white_background',images=['2'],confidence=.95),
        dict(attribute='listingImageRole',value='dimensions',images=['24'],confidence=.9),
        dict(attribute='listingImageRole',value='white_background',images=['4'],confidence=.4)])
    assert image_urls(row['visible'])[:2]==[urls[2],urls[24]]
    assert len(image_urls(row['visible']))==8 and urls[3] not in image_urls(row['visible'])
    assert urls==original and report['state']=='classified'
    before=copy.deepcopy(row)
    normalize(row['visible']);assert row==before


def test_image_limit_save_reopen_identity_and_locked_submitted_rows(publishing):
    c,catalog,_,_,b,base,_=publishing
    doc=ready(publishing);original=catalog.export();before=copy.deepcopy(doc['rows'])
    for row in doc['rows']:
        row['visible'].update(shortDescription='Soft fabric. Width 24.5 inches. Blue finish.',keyFeatures=['Soft fabric.'])
        row['visible']['productSecondaryImageURL']=[f'https://example.com/{i}.jpg' for i in range(24)]
    response=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')})
    assert response.status_code==200,response.text
    saved=response.json()
    for old,row in zip(before,saved['rows']):
        assert len(image_urls(row['visible']))==8
        assert text(row['visible']['shortDescription'])=='Features:\n1. Width 24.5 inches.\n2. Blue finish.'
        for key in ('listing_id','platform_sku','source_sku','identification','orderable'):
            assert row[key]==old[key]
    assert c.get(base).json()['rows']==saved['rows'] and catalog.export()==original
    again=c.patch(base,json={k:saved[k] for k in ('revision','version','rows')}).json()
    assert again['revision']==saved['revision'] and again['rows']==saved['rows']


def test_store_history_distinguishes_drafts_from_real_submission_results(publishing):
    c,catalog,_,_,b,base,_=publishing
    doc=ready(publishing)
    lid=doc['rows'][0]['listing_id']
    history=c.get('/api/products/'+lid).json()['store_history']
    assert len(history)==1 and set(history[0]['counts'])=={'draft'}
    assert history[0]['store_name']==b['preview']['store']['name']
    aid=send(publishing,doc)['id']
    with catalog.connect() as db:
        results={r['platform_sku']:dict(status='published' if r['listing_id']==lid else 'rejected',message='',errors=[]) for r in doc['rows']}
        db.execute('UPDATE walmart_listing_attempts SET results_json=? WHERE id=?',(json.dumps(results),aid))
    history=c.get('/api/products/'+lid).json()['store_history']
    assert history[0]['counts']=={'published':1}
    assert c.get(base).json()['basis']['items'][0]['store_history']==history
    listing=c.get('/api/products').json()['items']
    assert next(i for i in listing if i['id']==lid)['store_history']==history
    # A removed failed batch must still retain actual submission history.
    with catalog.connect() as db:
        db.execute("UPDATE prelisting_batches SET status='cancelled' WHERE id=?",(b['id'],))
    assert c.get('/api/products/'+lid).json()['store_history']==history


def test_skill_sections_cleanup_and_verified_specs_are_idempotent():
    from catalog.listing_content import source_specifications
    visible=dict(keyFeatures=['Soft fabric.'],shortDescription=(
        'Features: 1.Soft fabric. 2. Indoor chair. Product Information: '
        'Width: 24.5 in.; Manufacturer: Old Factory; Warranty: 2 years; UPC: 012345678905; Material: Wood'))
    facts={'content.attributes':{'Color':'Blue','Manufacturer':'Old Factory','Model Number':'Fake123','Width':'24.5 in.'}}
    source_specifications(visible,facts);normalize(visible)
    description=visible['shortDescription']
    assert 'Features:' in description and 'Product Information:' in description
    assert 'Soft fabric' not in description and 'Manufacturer' not in description and 'Warranty' not in description and '012345678905' not in description
    assert '24.5 in.' in description and 'Material: Wood' in description and 'Color: Blue' in description
    before=copy.deepcopy(visible)
    source_specifications(visible,facts);normalize(visible)
    assert visible==before


def test_previous_validation_cannot_bypass_new_content_policy(publishing):
    c,catalog,_,_,_,base,_=publishing
    doc=ready(publishing)
    assert doc['validation']['current']
    with catalog.connect() as db:
        stored=db.execute('SELECT validation_json FROM walmart_listing_documents WHERE batch_id=?',(base.split('/')[-2],)).fetchone()
        validation=json.loads(stored[0]);validation.pop('content_policy')
        db.execute('UPDATE walmart_listing_documents SET validation_json=? WHERE batch_id=?',(json.dumps(validation),base.split('/')[-2]))
    assert not c.get(base).json()['validation']['current']


def damaged_bed_description():
    return '''**Features:**
1\\. Product Information
2\\. Product Type
3\\. French bed
4\\. Size
5\\. Natural
6\\. Color
7\\. White
8\\. Material (main)
9\\. Pine+Plywood
10\\. Overall Product Dimension
11\\. 11.
12\\. 12.
21\\. 78.19'' x 41.77'' x 21.46''
22\\. Package number
23\\. 22.
24\\. 21.
42\\. 1
43\\. Package Contents
44\\. 41.
61\\. 1 x Bed, 1 x Accessory Kit 1 x Instructions
62\\. Overall structure of fence
63\\. The floor fence bed is more stable.
**Product Information:**
1\\. Bed Size: Twin
2\\. Assembled Length: 78.19 inches
3\\. Place of Origin: Viet Nam
'''


def test_corrupted_bed_description_is_repaired_once_and_never_accumulates_numbers():
    from catalog.listing_content import description_sections
    visible=dict(shortDescription=damaged_bed_description(),keyFeatures=['The floor fence bed is more stable.'])
    normalize(visible)
    expected=copy.deepcopy(visible)
    for _ in range(20):
        normalize(visible)
        assert visible==expected
    sections=description_sections(visible['shortDescription'])
    info=sections['Product Information']
    assert 'Product Type: French bed' in info
    assert 'Color: White' in info
    assert "Overall Product Dimension: 78.19'' x 41.77'' x 21.46''" in info
    assert 'Package number: 1' in info
    assert 'Package Contents: 1 x Bed, 1 x Accessory Kit 1 x Instructions' in info
    assert 'Bed Size: Twin' in info
    assert not any(p.rstrip('.').isdigit() for points in sections.values() for p in points)
    assert visible['shortDescription'].count('Product Information:')==1
    assert 'The floor fence bed' not in visible['shortDescription']


def test_rebuild_uses_source_table_and_replaces_parameters_not_generated_output():
    from catalog.listing_content import source_specifications, description_sections
    facts={'content.copy':{'description':'''<table><tr><th>Product Type</th><td>French bed</td></tr>
        <tr><td>Overall Product Dimension</td><td>78.19'' x 41.77'' x 21.46''</td></tr>
        <tr><td>Package number</td><td>1</td></tr><tr><td>Color</td><td>White</td></tr></table>
        <p>Solid wood fence.</p><p>SourceBrand design.</p>'''},
        'content.attributes':{'Bed Size':'Twin','Color':'White'},'source_brands':['SourceBrand']}
    original=copy.deepcopy(facts)
    visible=dict(brand='TargetBrand',shortDescription=damaged_bed_description(),keyFeatures=['Solid wood fence.'],
        productName='TargetBrand bed',mainImageUrl='original-image',custom='keep')
    unchanged={k:v for k,v in visible.items() if k!='shortDescription'}
    source_specifications(visible,facts);normalize(visible)
    expected=copy.deepcopy(visible)
    for _ in range(20):
        source_specifications(visible,facts);normalize(visible)
        assert visible==expected
    assert facts==original
    assert all(visible[k]==v for k,v in unchanged.items())
    assert 'TargetBrand design.' in visible['shortDescription']
    assert 'SourceBrand' not in visible['shortDescription']
    assert 'Place of Origin' not in visible['shortDescription']
    assert 'Solid wood fence.' not in visible['shortDescription']
    facts['content.attributes']['Color']='Black'
    source_specifications(visible,facts);normalize(visible)
    info=description_sections(visible['shortDescription'])['Product Information']
    assert 'Color: Black' in info and not any('White' in p for p in info)


def test_bed_cleanup_and_fixed_origin_survive_save_reopen_without_other_changes(publishing):
    from test_walmart_publishing import PT
    from catalog.walmart_listing_schema import sections
    c,catalog,_,_,_,base,remote=publishing
    origin='country_of_origin_substantial_transformation'
    sections(remote.schema,PT)['orderable']['properties'][origin]={'type':'string','enum':['China','Viet Nam']}
    doc=ready(publishing)
    source=catalog.export()
    before=copy.deepcopy(doc['rows'])
    for row in doc['rows']:
        row['visible']['shortDescription']=damaged_bed_description()
        row['orderable'][origin]='Viet Nam'
    saved=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')})
    assert saved.status_code==200,saved.text
    saved=saved.json()
    for row,old in zip(saved['rows'],before):
        assert row['orderable'][origin]=='China'
        assert row['orderable']==old['orderable']
        assert row['visible']['shortDescription'].count('Product Information:')==1
        for key in ('platform_sku','source_sku','listing_id','identification','fulfillment_center_id'):
            assert row[key]==old[key]
        assert {k:v for k,v in row['visible'].items() if k!='shortDescription'}=={k:v for k,v in old['visible'].items() if k!='shortDescription'}
    for _ in range(3):
        reopened=c.get(base).json()
        assert reopened['rows']==saved['rows']
        again=c.patch(base,json={k:reopened[k] for k in ('revision','version','rows')}).json()
        assert again['revision']==saved['revision'] and again['rows']==saved['rows']
    assert catalog.export()==source
    with catalog.connect() as db:
        events=db.execute("SELECT details_json FROM walmart_listing_events WHERE action='edit'").fetchall()
        assert any('user_authorized_rule' in e[0] for e in events)
