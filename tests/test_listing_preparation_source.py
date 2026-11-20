import json

from catalog.images import HumanVerificationRequired
from catalog.listing_generation_jobs import start_job, run_job
from test_listing_generation import AI
from test_listing_price_review import amazon
from test_walmart_publishing import publishing, submission
from test_giga_prelisting import warehouse
from test_giga import settings
from test_walmart_accounts import vault
from test_source_capture import page, offer


def patch_rows(c, base, change):
    doc=c.get(base).json()
    change(doc['rows'])
    result=c.patch(base,json={k:doc[k] for k in ('revision','version','rows')})
    assert result.status_code==200,result.text
    return result.json()


def empty_prices(rows):
    for row in rows:
        row['orderable']['price']=0
        row['orderable'].pop('msrp',None)


def execute(env, ai=None):
    c,catalog,_,_,b,base,_=env
    ai=ai or AI()
    job=start_job(catalog,c.app.state.walmart_publisher,b['id'],ai=ai,schedule=False)
    assert start_job(catalog,c.app.state.walmart_publisher,b['id'],ai=ai,schedule=False)['id']==job['id']
    run_job(catalog,c.app.state.walmart_publisher,job['id'],ai)
    status=c.get(base+'/generation').json()['job']
    return c.get(base).json(),status


def test_quotes_all_children_then_price_then_ai_and_reuse(amazon,monkeypatch):
    c,catalog,_,_,b,base,remote=amazon
    patch_rows(c,base,empty_prices)
    fetched=[]
    def fetch(asin):
        fetched.append(asin)
        return page(asin,offer(original='72.89' if asin.endswith('70') else '80.00'))
    monkeypatch.setattr('catalog.images.fetch_amazon_page',fetch)
    ai=AI()
    def before_ai():
        assert len(fetched)==2
        rows=c.get(base).json()['rows']
        assert sorted(r['orderable']['price'] for r in rows)==[87.47,96]
    ai.hook=before_ai
    doc,status=execute(amazon,ai)
    assert status['state']=='complete',status
    assert len(fetched)==2 and len(set(fetched))==2
    assert sorted(r['orderable']['price'] for r in doc['rows'])==[87.47,96]
    assert all(r['orderable']['msrp']>0 for r in doc['rows'])
    assert all(r['visible'].get('assembledProductHeight') for r in doc['rows'])
    with catalog.connect() as db:
        captures=db.execute('SELECT variant_sku,data_json FROM source_captures').fetchall()
        assert {r[0] for r in captures}=={'child-70','child-71'}
        assert all(json.loads(r[1])['method']=='generation_background' for r in captures)
    # Existing captures are reusable, no second network fetch or blank prices.
    again,_=execute(amazon)
    assert len(fetched)==2
    assert [r['orderable'] for r in again['rows']]==[r['orderable'] for r in doc['rows']]
    assert not remote.posts and not remote.stock


def test_captcha_does_not_block_other_child_or_ai(amazon,monkeypatch):
    c,_,_,_,_,base,_=amazon
    patch_rows(c,base,empty_prices)
    def fetch(asin):
        if asin.endswith('70'): raise HumanVerificationRequired('captcha')
        return page(asin,offer(original='80.00'))
    monkeypatch.setattr('catalog.images.fetch_amazon_page',fetch)
    doc,status=execute(amazon)
    assert status['state']=='complete',status
    rows={r['source_sku']:r for r in doc['rows']}
    assert 'price' not in rows['child-70']['orderable']
    assert rows['child-71']['orderable']['price']==96
    assert all(r['visible'].get('assembledProductHeight') for r in rows.values())
    report=status['report']['rows'][rows['child-70']['platform_sku']]
    assert report['collection']['state']=='verification_required'
    assert 'price' in str(doc['validation']['rows'][rows['child-70']['platform_sku']]['errors'])


def test_manual_price_msrp_and_submitted_rows_preserved(amazon,monkeypatch):
    c,_,_,_,_,base,remote=amazon
    def change(rows):
        rows[0]['orderable'].update(price=123.45,msrp=150)
        rows[1]['orderable'].update(price=222,msrp=250)
    before=patch_rows(c,base,change)
    monkeypatch.setattr('catalog.images.fetch_amazon_page',lambda asin:page(asin,offer(original='72.89')))
    doc,status=execute(amazon)
    assert status['state']=='complete',status
    assert [(r['orderable']['price'],r['orderable']['msrp']) for r in doc['rows']]==[(123.45,150),(222,250)]
    submitted=c.post(base+'/submit',json=submission(doc,skus=[doc['rows'][0]['platform_sku']]))
    assert submitted.status_code==200,submitted.text
    after=c.get(base).json()
    again,status=execute(amazon)
    assert again['rows'][0]==after['rows'][0]
    assert len(remote.posts)==1


def test_concurrent_edit_during_fetch_stops_capture_and_reprice(amazon,monkeypatch):
    c,catalog,_,_,_,base,_=amazon
    patch_rows(c,base,empty_prices)
    def fetch(asin):
        patch_rows(c,base,lambda rows:rows[0]['orderable'].update(price=333))
        return page(asin,offer())
    monkeypatch.setattr('catalog.images.fetch_amazon_page',fetch)
    doc,status=execute(amazon)
    assert status['state']=='failed'
    assert doc['rows'][0]['orderable']['price']==333
    with catalog.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM source_captures').fetchone()[0]==0


def test_capture_saved_in_catalog_before_generation_rebases_old_document(amazon,monkeypatch):
    c,_,_,_,_,base,_=amazon
    before=patch_rows(c,base,empty_prices)
    fetched=[]
    def fetch(asin):
        fetched.append(asin)
        return page(asin,offer(original='72.89'))
    monkeypatch.setattr('catalog.images.fetch_amazon_page',fetch)
    monkeypatch.setattr('catalog.source_capture.fetch_amazon_page',fetch)
    for row in before['rows']:
        result=c.post(f"/api/products/{row['listing_id']}/source-capture",json={'variant_sku':row['source_sku'],'background':True})
        assert result.status_code==200,result.text
    assert len(fetched)==2
    doc,status=execute(amazon)
    assert status['state']=='complete',status
    assert len(fetched)==2
    assert all(row['orderable']['price']==87.47 for row in doc['rows'])


def test_two_variants_of_one_product_use_their_own_captured_prices(publishing,monkeypatch):
    from catalog.models import ImportBundle
    from test_prelisting import product, inputs, create
    from test_walmart_publishing import ready
    c,catalog,fake,settings,_,_,remote=publishing
    parent=product(70,brand='NewBrand')
    parent.variants.append(product(71).variants[0])
    for v,ean in zip(parent.variants,['012345678905','012345678912']):
        v.source_facts={'asin':v.asin,'currency':'USD'}
        v.ean=ean;v.copy.title='Wood chair';v.images=['https://example.com/chair.jpg']
    catalog.ingest(ImportBundle(namespace='two-children',label='test',listings=[parent]))
    b,_=create(c,inputs(c,filters={'source':'Amazon'}))
    base=f"/api/prelisting/batches/{b['id']}/walmart"
    assert c.post(base+'/prepare').status_code==200
    env=(c,catalog,fake,settings,b,base,remote)
    ready(env)
    patch_rows(c,base,empty_prices)
    fetched=[]
    def fetch(asin):
        fetched.append(asin)
        return page(asin,offer(original='72.89' if asin.endswith('70') else '80.00'))
    monkeypatch.setattr('catalog.images.fetch_amazon_page',fetch)
    doc,status=execute(env)
    assert status['state']=='complete',status
    assert len(fetched)==2
    assert {r['source_sku']:r['orderable']['price'] for r in doc['rows']}=={'child-70':87.47,'child-71':96}
    assert len({r['listing_id'] for r in doc['rows']})==1


def test_wraith_recovery_flows_into_pricing_and_ai(amazon,monkeypatch):
    c,_,_,_,_,base,_=amazon
    patch_rows(c,base,empty_prices)
    def blocked(asin):raise HumanVerificationRequired('Amazon 要求人机验证')
    monkeypatch.setattr('catalog.images.fetch_amazon_page',blocked)
    monkeypatch.setenv('CATALOG_AMAZON_BROWSER_PYTHON','configured')
    monkeypatch.setattr('catalog.amazon_browser.fetch_page',lambda asin:page(asin,offer(original='72.89')))
    doc,status=execute(amazon)
    assert status['state']=='complete',status
    assert all(row['orderable']['price']==87.47 for row in doc['rows'])
    assert all(row['source_collection']['method']=='wraith_background' for row in status['report']['rows'].values())


def test_confirmed_manual_price_is_not_collected_again(amazon,monkeypatch):
    c,_,_,_,_,base,_=amazon
    doc=c.get(base).json()
    for row in doc['rows']:
        result=c.post(base+'/verify-source-price',json=dict(revision=doc['revision'],platform_sku=row['platform_sku'],original_price='72.89'))
        assert result.status_code==200,result.text
        doc=result.json()
    def fail(asin):raise AssertionError('confirmed price should not be fetched again')
    monkeypatch.setattr('catalog.images.fetch_amazon_page',fail)
    after,status=execute(amazon)
    assert status['state']=='complete',status
    assert all(row['orderable']['price']==87.47 for row in after['rows'])
    assert len(after['source_price_reviews'])==2
