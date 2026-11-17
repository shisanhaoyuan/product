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
