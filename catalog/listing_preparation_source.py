"""Ordered source collection and deterministic pricing before AI touches listing text."""
import copy
import json
import time
import uuid
from decimal import Decimal, InvalidOperation

from .database import write_lock
from .prelisting import encoded
from .walmart_accounts import now
from .listing_ai_client import ListingAIError


def positive(value):
    try:
        return Decimal(str(value)).is_finite() and Decimal(str(value)) > 0
    except (InvalidOperation, ValueError):
        return False


def refresh_amazon(catalog, db, doc, actor_id=None, job_id=None):
    from .walmart_publishing import batch, document, fresh_basis, latest_results, EDITABLE
    old=json.loads(doc['basis_json'])
    if not any(i.get('source')=='Amazon' for i in old['items']):
        return doc
    current=fresh_basis(catalog,db,batch(db,doc['batch_id']))
    fresh={i['listing_id']:i for i in current['items']}
    basis=copy.deepcopy(old)
    rows=json.loads(doc['rows_json']);statuses=latest_results(db,doc['batch_id'])
    changes=[]
    for n,item in enumerate(old['items']):
        if item.get('source')!='Amazon':
            continue
        revised=fresh[item['listing_id']]
        old_variants={v['platform_sku']:v for v in item['variants']}
        new_variants={v['platform_sku']:v for v in revised['variants']}
        for row in (r for r in rows if r['listing_id']==item['listing_id']):
            if statuses.get(row['platform_sku'],{}).get('status','unsubmitted') not in EDITABLE:
                continue
            previous=old_variants[row['platform_sku']];new=new_variants[row['platform_sku']]
            price=row['orderable'].get('price')
            # Preserve deliberate positive prices; only fill blanks/zero or refresh a calculated value.
            automatic=not positive(price) or (positive(previous.get('price')) and Decimal(str(price))==Decimal(str(previous['price'])))
            before=copy.deepcopy(row['orderable'])
            if automatic:
                if positive(new.get('price')):
                    row['orderable']['price']=float(new['price'])
                else:
                    row['orderable'].pop('price',None)
            if not positive(row['orderable'].get('msrp')) and positive(revised.get('msrp')):
                row['orderable']['msrp']=float(revised['msrp'])
            if before!=row['orderable']:
                changes.append(dict(platform_sku=row['platform_sku'],before=before,after=row['orderable']))
        basis['items'][n]=revised
    if basis!=old or changes:
        db.execute('UPDATE walmart_listing_documents SET revision=revision+1,basis_json=?,rows_json=?,validation_json=?,updated_at=? WHERE batch_id=?',
                   (encoded(basis),encoded(rows),'{}',now(),doc['batch_id']))
        db.execute('INSERT INTO walmart_listing_events VALUES(?,?,?,?,?,?)',
                   (uuid.uuid4().hex,doc['batch_id'],actor_id,'prepare_source_prices',now(),encoded(dict(job_id=job_id,changes=changes))))
    return document(db,doc['batch_id'])


def collect_before_generation(catalog, job, report, progress, checked_context):
    from .source_capture import collect_quote_page, save_capture
    from .images import fetch_amazon_page, HumanVerificationRequired
    from .walmart_publishing import document, latest_results, EDITABLE
    with catalog.connect() as db:
        stored,_=checked_context(db,job)
        basis=json.loads(stored['basis_json']);statuses=latest_results(db,job['batch_id'])
        from .listing_price_review import reviews
        verified=reviews(db,stored)
    targets=[(i,v) for i in basis['items'] if i.get('source')=='Amazon' for v in i['variants']
             if v.get('capture_required') and v['platform_sku'] not in verified and statuses.get(v['platform_sku'],{}).get('status','unsubmitted') in EDITABLE]
    for index,(item,variant) in enumerate(targets):
        sku=variant['platform_sku'];asin=variant['asin']
        progress(sku,'collecting',f'后台采集报价 {index+1}/{len(targets)}：{variant["sku"]}')
        captured=None
        try:
            html,quote,method=collect_quote_page(asin,fetch_amazon_page)
            from .listing_source_evidence import amazon_details
            facts=amazon_details(html,asin)
            captured=quote
            message=('后台报价采集完成' if quote['status']=='captured' else
                     '后台报价尚未完整核验；继续 AI 填充，售价可人工核对')
            outcome=dict(state=quote['status'],message=message,asin=asin,facts=facts,method=method)
        except HumanVerificationRequired as exc:
            outcome=dict(state='verification_required',message=str(exc)+'；继续 AI 填充，售价可人工核对',asin=asin)
        except (ValueError, OSError, ListingAIError) as exc:
            outcome=dict(state='failed',message='后台报价采集失败，继续 AI 填充；'+str(exc)[:200],asin=asin)
        with catalog.connect() as db:
            write_lock(db);stored,_=checked_context(db,job)
            if captured is not None and captured.get('status') in ('captured','partial'):
                save_capture(catalog,db,item['listing_id'],variant.get('capture_sku') or None,asin,captured,
                             job.get('actor_id'),f'https://www.amazon.com/dp/{asin}?th=1&psc=1','wraith_background' if method=='wraith_background' else 'generation_background')
                stored=refresh_amazon(catalog,db,stored,job.get('actor_id'),job['id'])
                job['revision']=stored['revision']
            report['rows'][sku]['source_collection']=outcome
            db.execute('UPDATE listing_generation_jobs SET revision=?,report_json=?,updated_at=? WHERE id=?',
                       (job['revision'],encoded(report),int(time.time()),job['id']))
    with catalog.connect() as db:
        stored,_=checked_context(db,job)
        return stored
