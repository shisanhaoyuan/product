"""Copy saved product content into an independent, target-store listing draft."""
import copy
import json
import os
import re
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from .listing_titles import branded_title, source_brands
from .prelisting import encoded, fingerprint


def target_variants(source, selected=None):
    variants=source.get('variants') or [source]
    return [v for v in variants if selected is None or v['sku'] in selected]


def resolve(db, listing_id, bid, source, selected=None):
    record=db.execute('''SELECT d.*,b.preview_json FROM walmart_listing_documents d
        JOIN prelisting_batches b ON b.id=d.batch_id
        LEFT JOIN prelisting_batch_trash t ON t.batch_id=b.id
        WHERE d.batch_id=? AND t.batch_id IS NULL''',(bid,)).fetchone()
    if not record:
        raise HTTPException(409,'所选模板不存在或已移入回收站，请重新选择')
    basis=json.loads(record['basis_json'])
    item=next((i for i in basis['items'] if i['listing_id']==listing_id),None)
    if not item or item['source']!=source['source']:
        raise HTTPException(409,'模板不属于该来源商品，不能跨商品复用')
    rows=[r for r in json.loads(record['rows_json']) if r['listing_id']==listing_id]
    by_sku={r['source_sku']:r for r in rows}
    evidence={v['sku']:v for v in item['variants']}
    if len(rows)!=len(by_sku):
        raise HTTPException(409,'模板子 SKU 对应关系重复，不能复用')
    matches=[]
    for variant in target_variants(source,selected):
        sku=variant['sku'];row=by_sku.get(sku);old=evidence.get(sku)
        if (not row or not old or row['platform_sku']!=old.get('platform_sku') or old.get('asin','')!=variant.get('asin','')
                or not row.get('product_type') or not row.get('visible',{}).get('productName','').strip()):
            raise HTTPException(409,'模板未覆盖所选子 SKU、身份已变化或尚未填写类目与标题，请更换模板')
        matches.append(row)
    preview=json.loads(record['preview_json'])
    info=dict(batch_id=bid,revision=record['revision'],version=record['version'],
              store_name=preview['store']['name'],brand=preview['brand'],
              multiplier=preview['multiplier'],
              updated_at=record['updated_at'],sku_count=len(matches),
              title=matches[0]['visible']['productName'],
              digest=fingerprint(dict(version=record['version'],rows=matches)))
    return info,matches


def template_options(db, records, data):
    # Read each document once, then only consider products actually in its frozen basis.
    candidates={r['id']:[] for r in records}
    marks=','.join('?' for _ in candidates)
    for doc in db.execute(f'''SELECT d.batch_id,d.basis_json FROM walmart_listing_documents d
            LEFT JOIN prelisting_batch_trash t ON t.batch_id=d.batch_id
            WHERE t.batch_id IS NULL AND EXISTS(SELECT 1 FROM prelisting_items i
                WHERE i.batch_id=d.batch_id AND i.listing_id IN ({marks}))
            ORDER BY d.updated_at DESC,d.batch_id DESC''',list(candidates)):
        for item in json.loads(doc['basis_json'])['items']:
            if item['listing_id'] in candidates:
                candidates[item['listing_id']].append(doc['batch_id'])
    result=[]
    for record in records:
        source=json.loads(record['data_json']);options=[]
        selected=set(data.variant_skus[record['id']]) if data.variant_skus is not None else None
        for bid in candidates[record['id']]:
            try:
                info,_=resolve(db,record['id'],bid,source,selected)
                options.append(info)
            except HTTPException:
                continue
        result.append(dict(listing_id=record['id'],title=source['copy']['title'],options=options))
    return dict(items=result)


def annotate_preview(db, data, preview, existing_bid=None):
    if not data.template_batches:
        return
    ids={i['listing_id'] for i in preview['items']}
    if not set(data.template_batches).issubset(ids):
        raise HTTPException(409,'模板选择包含本批次之外的商品')
    frozen={}
    if existing_bid:
        record=db.execute('SELECT preview_json FROM prelisting_batches WHERE id=?',(existing_bid,)).fetchone()
        frozen={i['listing_id']:i.get('reuse_template') for i in json.loads(record[0])['items']}
    versions=set()
    for item in preview['items']:
        bid=data.template_batches.get(item['listing_id'])
        if not bid:
            continue
        if existing_bid:
            info=frozen.get(item['listing_id'])
            if not info or info['batch_id']!=bid:
                raise HTTPException(409,'已创建批次的模板来源不一致')
        else:
            source=json.loads(db.execute('SELECT data_json FROM current_listings WHERE id=?',(item['listing_id'],)).fetchone()[0])
            info,_=resolve(db,item['listing_id'],bid,source,{v['sku'] for v in item['variants']})
        item['reuse_template']=info
        versions.add(info['version'])
    if len(versions)>1:
        raise HTTPException(409,'所选模板的沃尔玛规格版本不同，请分批复用')


def replace_brand(value, brands, target):
    if isinstance(value,list):
        return [replace_brand(v,brands,target) for v in value]
    if not isinstance(value,str):
        return value
    patterns=[]
    for brand in sorted(set(b for b in brands if b and b.casefold()!=target.casefold()),key=len,reverse=True):
        pattern=re.escape(brand).replace(r'\ ',r'\s+')
        pattern=(r'(?<!\w)' if brand[0].isascii() and brand[0].isalnum() else '')+pattern
        pattern+=r'(?!\w)' if brand[-1].isascii() and brand[-1].isalnum() else ''
        patterns.append(pattern)
    return re.sub('|'.join(patterns),lambda _:target,value,flags=re.I) if patterns else value


def create_document(catalog, db, bid, data, actor_id):
    """Called inside batch creation's write transaction. No remote calls or submission."""
    from .walmart_publishing import fresh_basis, default_rows, ListingRow
    from .listing_prerequisites import allocate_document_identifiers, preparation_settings
    current=db.execute('SELECT * FROM prelisting_batches WHERE id=?',(bid,)).fetchone()
    basis=fresh_basis(catalog,db,current);rows=default_rows(basis);copies=[]
    by_identity={(r['listing_id'],r['source_sku']):r for r in rows}
    bound=db.execute('SELECT * FROM walmart_accounts WHERE store_id=?',(data.store_id,)).fetchone()
    node=preparation_settings(db,bound).get('fulfillment_center_id','') if bound and bound['status']=='verified' else ''
    version=os.environ.get('CATALOG_WALMART_SPEC_VERSION','')
    for item in basis['items']:
        info=item.get('reuse_template')
        if not info:
            continue
        source=json.loads(db.execute('SELECT data_json FROM current_listings WHERE id=?',(item['listing_id'],)).fetchone()[0])
        actual,templates=resolve(db,item['listing_id'],info['batch_id'],source,{v['sku'] for v in item['variants']})
        if actual!=info:
            raise HTTPException(409,'模板已变化，请重新预览')
        version=info['version']
        for template in templates:
            row=by_identity[(item['listing_id'],template['source_sku'])]
            old_brand=template['visible'].get('brand') or info['brand']
            brands=[old_brand,info['brand'],*source_brands(source)]
            row['product_type']=template['product_type']
            row['visible']=copy.deepcopy(template['visible'])
            # Group routing belongs to the destination store, never to the template.
            from .listing_variants import SYSTEM_FIELDS
            for key in SYSTEM_FIELDS:
                row['visible'].pop(key,None)
            row['visible']['brand']=data.brand
            row['visible']['productName']=branded_title(row['visible'].get('productName',''),data.brand,brands)
            for key in ('shortDescription','keyFeatures'):
                if key in row['visible']:
                    row['visible'][key]=replace_brand(row['visible'][key],brands,data.brand)
            # Content fields may be copied; price, identifiers, routing and inventory may not.
            row['orderable']={k:copy.deepcopy(v) for k,v in template['orderable'].items()
                if k not in {'sku','price','productIdentifiers','inventory','SkuUpdate','ProductIdUpdate','fulfillmentCenterID','shipNode','specProductType'}} | {
                    k:v for k,v in row['orderable'].items() if k=='price'}
            row['fulfillment_center_id']=node
            if old_brand.casefold()!=data.brand.casefold() and not next(v for v in item['variants'] if v['sku']==row['source_sku']).get('assigned_identifier',{}).get('value'):
                row['identification']=dict(mode='identifier',type='EAN',value='',submission_id='')
            from .listing_content import normalize
            normalize(row['visible'])
            ListingRow.model_validate(row)
            copies.append(dict(source_batch_id=info['batch_id'],source_revision=info['revision'],
                listing_id=row['listing_id'],source_sku=row['source_sku'],platform_sku=row['platform_sku']))
    from .walmart_listing_schema import DEFAULT_VERSION
    at=datetime.now(timezone.utc).isoformat()
    db.execute('INSERT INTO walmart_listing_documents VALUES(?,?,?,?,?,?,?)',
        (bid,1,version or DEFAULT_VERSION,encoded(rows),encoded(basis),'{}',at))
    db.execute('INSERT INTO walmart_listing_events VALUES(?,?,?,?,?,?)',
        (uuid.uuid4().hex,bid,actor_id,'reuse_template',at,encoded(dict(copies=copies))))
    doc=db.execute('SELECT * FROM walmart_listing_documents WHERE batch_id=?',(bid,)).fetchone()
    allocate_document_identifiers(db,dict(doc),actor_id)
