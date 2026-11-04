"""Read only allowlisted, local DigitEmperor audit files. Never calls the ERP.

Provenance is selected before normalization. Repairs require matching identity and
source digest. Persisted data is never synthesized from a proposed write plan.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from ..models import ImportBundle, Listing, Variant, Copy


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def objects(data):
    return data if isinstance(data,list) else [data]


def timestamp(path):
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def asin_from_url(url):
    m=re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)",url or '')
    return m.group(1) if m else ''


def url_ok(url):
    u=urlparse(str(url))
    return u.scheme in {'http','https'} and bool(u.hostname) and not u.username and not u.password


def product_images(values):
    # capture_product_state includes navigation icons: only actual product CDN paths.
    out=[]
    for v in values:
        if not isinstance(v,str) or not url_ok(v):continue
        u=urlparse(v)
        if (u.hostname in {'pg-cdn-a2.datacaciques.com','ps-cdn-s3.datacaciques.com',
                           'pg-cdn.datacaciques.com','m.media-amazon.com','images-na.ssl-images-amazon.com'}
                and '/img/' not in u.path):
            if v not in out:out.append(v)
    return out[:100]


def copy_from_state(state):
    c=state.get('main_copy',{})
    return Copy(title='\n'.join(c.get('dc_title',[])),description='\n'.join(c.get('dc_description',[])),
                features=c.get('Key Features',[]))


def schema_state(schema):
    fields={};copy={k:[] for k in ['dc_title','dc_description','Key Features']}
    for f in schema.get('fields',[]):
        key=f.get('field_id','');value=f.get('current_value',f.get('value',''))
        if key in copy:
            if isinstance(value,str):copy[key].append(value)
        elif f.get('visible') is not False and key and not key.startswith(('control-','s2id_','dc_module')):
            if key not in {'ListingStageAttrsPlugin','dc_multiPropProduct','PropsCombine'}:fields[key]=value
    return dict(fields=fields,main_copy=copy,rows={},images=[],management=[])


def saved(erp):
    return all(erp.get(k,{}).get('status')=='saved' for k in ('product_information_save','product_library_save'))


def same_template(t, item):
    return isinstance(t,dict) and all(str(t.get(k,''))==str(item.get(k,'')) for k in ('sku','template','record_id'))


def valid_readback(rb,item,store):
    return (isinstance(rb,dict) and rb.get('reopened_on_new_page') is True and isinstance(rb.get('state'),dict)
            and same_template(rb.get('template'),item)
            and rb['state'].get('fields',{}).get('templetName')==item['template']
            and rb['state'].get('fields',{}).get('dc_platformAccount')==store)


def explicit_seller(offer):
    for key in ('seller_name','sold_by','merchant_name','seller'):
        value=offer.get(key)
        if isinstance(value,str) and value.strip():return value.strip()
        if isinstance(value,dict) and isinstance(value.get('name'),str):return value['name']
    return ''


def source_facts(offer):
    # Keep a small allowlist, never include raw pages, credentials or arbitrary logs.
    return {k:offer[k] for k in ('asin','url','price','currency','shipping','price_reference',
        'availability_text','main_page_stock','identity_verified','delivery_verified','offer_scope',
        'offer_ambiguous','title','raw_attributes','bullet_points') if k in offer}


def normalize(audit,item,params,rb,kind,evidence_path,observed,save_success,complete=False,issues=None):
    src=audit.get('source',{});erp=audit.get('erp_fill_audit',{});amazon=audit.get('amazon_raw',{})
    if src.get('sku')!=item['sku'] or audit.get('runtime_parameters')!=params:
        raise ValueError('审核文件 SKU 或本轮参数不匹配')
    asin=src.get('asin','')
    url=src.get('erp_product_source_url') or src.get('url','')
    if asin_from_url(url) and asin_from_url(url)!=asin:raise ValueError('父 ASIN 与来源链接冲突')
    if kind=='persisted_readback' or rb.get('snapshot_origin')=='initial_persisted_state':state=rb['state']
    else:state=schema_state(erp.get('schema_after_fill') or erp.get('schema_after') or audit.get('erp_schema',{}))
    fields=state.get('fields',{})
    if kind=='persisted_readback' and fields.get('Brand Name') not in (None,params['brand']):
        # Show actual value and flag discrepancy rather than change it to requested brand.
        issues=[*(issues or []),{'section':'identity','reason':'回读品牌与本轮参数不同'}]
    prices=erp.get('multi_variant_runtime',{}).get('prices',{}).get('filled',[])
    price_by_sku={p.get('sku'):p for p in prices}
    checks={p.get('sku'):p for p in rb.get('pricing_checks',[]) if isinstance(p,dict)}
    stocks={p.get('sku'):p for p in rb.get('stock_checks',[]) if isinstance(p,dict)}
    source_by_asin=amazon.get('variant_copy',{}).get('by_asin',{})
    offers=amazon.get('variant_copy',{}).get('offers_by_asin',{})
    basis=audit.get('pricing_basis_cache',{}).get('variants',{})
    # Older audits recorded source SKUs before brand replacement. Join the two
    # identities only through an explicit verified rename in that same audit.
    cell_groups={}
    for cell in erp.get('audit',{}).get('listing_defaults',{}).get('cells',[]):
        if cell.get('status')=='verified':cell_groups.setdefault(cell.get('row'),[]).append(cell)
    associations={}
    source_stock={}
    for group in cell_groups.values():
        renamed={c.get('expected') for c in group if c.get('field')=='sku alias' and c.get('expected')}
        asins={c.get('source_asin') for c in group if c.get('source_asin')}
        asins.update(c['stock_decision']['asin'] for c in group if c.get('stock_decision',{}).get('asin'))
        if len(renamed)==1 and len(asins)==1:
            target=next(iter(renamed));original=next(iter(asins))
            if target in associations and associations[target]!=original:raise ValueError('同一平台 SKU 关联多个源 ASIN')
            associations[target]=original
            if original in price_by_sku and target not in price_by_sku:price_by_sku[target]=price_by_sku[original]
            decision=next((c['stock_decision'] for c in group if c.get('stock_decision')),None)
            if decision:source_stock[target]=decision
    variants=[]
    for sku,cells in state.get('rows',{}).items():
        merged={};attributes={};images=[]
        for cell in cells.values():
            value=cell.get('value',{})
            if isinstance(value,dict):
                merged.update(value)
                for key in ('Color','Size','Pattern','Finish','Theme','Shape','Snare Drum Size','Drum Shell Color','Frame Color'):
                    if key in value:attributes[key]=value[key]
                if isinstance(value.get('mainPhotos'),dict):images.extend(value['mainPhotos'].values())
            if cell.get('label')=='图片美化':images.extend(cell.get('images',[]))
        if merged.get('sku alias',merged.get('dc_platformSku',sku))!=sku:
            raise ValueError('子 SKU 与表格内值不一致')
        check=checks.get(sku,{});price=price_by_sku.get(sku,{})
        candidates={v for v in [check.get('price_reference',{}).get('asin'),
            asin_from_url(price.get('source_url','')),associations.get(sku),
            sku if sku in source_by_asin or sku in offers or sku in basis else None] if v}
        if len(candidates)>1:raise ValueError('子 SKU 的来源 ASIN 证据冲突')
        child_asin=next(iter(candidates),'')
        # Only explicit SKU associations are accepted, never reconstruct ASIN from SKU suffix.
        identity_note='' if child_asin else '原记录缺少明确的 SKU→ASIN 关联，未推测补写'
        offer=offers.get(child_asin,{})
        if not isinstance(offer,dict):offer={}
        fact=source_facts(offer)
        if sku in source_stock:fact['stock_decision']=source_stock[sku]
        if child_asin in source_by_asin:
            fact['copy']=source_facts(source_by_asin[child_asin])
        if child_asin in basis:fact['frozen_price_basis']=basis[child_asin]
        feature=merged.get('Key Features',{})
        features=feature.get('val',[]) if isinstance(feature,dict) else feature if isinstance(feature,list) else [str(feature)]
        variants.append(Variant(sku=sku,asin=child_asin,source_url=(price.get('source_url') or offer.get('url') or (f'https://www.amazon.com/dp/{child_asin}' if child_asin else '')),
            seller=explicit_seller(offer),attributes=attributes,images=product_images(images),
            price=str(merged['price']) if 'price' in merged else None,stock=str(merged['quantity']) if 'quantity' in merged else None,
            ean=str(merged.get('product id','')),copy=Copy(title=str(merged.get('title','')),
            description=str(merged.get('description','')),features=features),source_facts=fact,
            price_check=check,stock_check=stocks.get(sku,{}),raw_cells=cells,identity_note=identity_note))
    verified=kind=='persisted_readback' and rb.get('status')=='verified'
    if kind!='persisted_readback':
        issues=[*(issues or []),{'section':'persistence','reason':'没有可用的独立产品库回读；当前为编辑快照，不代表已保存内容'}]
    external='|'.join([params['walmart_account'],str(item.get('record_id','')),item['template']])
    return Listing(external_id=external,asin=asin,sku=item['sku'],platform_sku=str(fields.get('dc_platformSku','')),
        source='Amazon',source_url=url if url_ok(url) else '',seller=explicit_seller(amazon),store=params['walmart_account'],
        brand=params['brand'],template=item['template'],record_id=str(item.get('record_id','')),
        creator=item.get('creator',''),uploaded_at=item.get('created',''),observed_at=observed,multiplier=params['price_multiplier'],
        evidence_kind=kind,save_succeeded=save_success,fill_complete=bool(complete and verified and save_success),readback_verified=verified,
        copy=copy_from_state(state),images=product_images(state.get('images',[])),
        price=str(fields['dc_price']) if 'dc_price' in fields else None,
        stock=str(fields['dc_initListingQuantity']) if 'dc_initListingQuantity' in fields else None,
        msrp=str(fields['MSRP']) if 'MSRP' in fields else None,ean=str(fields.get('dc_productId','')),
        fields=fields,management=state.get('management',[]),variants=variants,source_facts=source_facts(amazon),
        issues=issues or [],checks={k:rb[k] for k in ('status','persistence','copy','pricing_checks','stock_checks','msrp_check','required_fields') if k in rb},
        evidence=dict(adapter='digitemperor-local-v1',file=evidence_path.name,relative_file=str(evidence_path),
            sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest(),timestamp_basis='local_file_mtime',
            note='历史证据导入时间戳来自文件修改时间；不是本次实时读取 ERP。',publish=False))


def build_bundle(log_root: Path):
    log_root=log_root.resolve();selected={};warnings=[];skipped_unattempted=0
    # Trusted local import only. Never executed by the web API; no arbitrary path endpoint.
    reports=list(log_root.glob('*/batch-report.json'))
    sources=[]
    for p in reports:
        data=read(p)
        for item in data.get('products',[]):
            ap=Path(item.get('audit_path',''))
            if not ap.is_absolute():ap=p.parent/ap
            sources.append((p.parent,item,dict(brand=item['brand'],walmart_account=item['store'],price_multiplier=str(item['multiplier'])),ap,item))
    report_dirs={p.parent for p in reports}
    # Manifests select exact templates; import only products with an actual audit.
    manifests=list(log_root.glob('*/manifest.json'))+list(log_root.glob('*/*/manifest.json'))
    for mp in manifests:
        if any(parent in report_dirs for parent in [mp.parent,*mp.parents]):continue
        if 'recovery' in mp.parts and (mp.parent/'audits').exists():continue
        m=read(mp)
        if not all(k in m for k in ('brand','walmart_account','price_multiplier')):continue
        params={k:str(m[k]) for k in ('brand','walmart_account','price_multiplier')}
        summary=read(mp.parent/'completion-summary.json') if (mp.parent/'completion-summary.json').exists() else {}
        summaries={x['sku']:x for x in summary.get('products',[])}
        for item in m.get('products',[]):
            ap=mp.parent/'audits'/f"{item['sku']}.json"
            if ap.exists():sources.append((mp.parent,item,params,ap,summaries.get(item['sku'],{})))
            else:skipped_unattempted+=1
    # Some early recovery queues use an external manifest, recorded in batch_state.
    for bp in log_root.glob('*/batch_state.json'):
        if (bp.parent/'manifest.json').exists() or any(p.parent==bp.parent for p in reports):continue
        meta=read(bp);mp=Path(meta.get('manifest',''))
        if not mp.is_file() or not (bp.parent/'audits').exists():continue
        m=read(mp);summary=read(bp.parent/'completion-summary.json') if (bp.parent/'completion-summary.json').exists() else {}
        scope=summary.get('scope',m)
        if not all(k in scope for k in ('brand','walmart_account','price_multiplier')):continue
        params={k:str(scope[k]) for k in ('brand','walmart_account','price_multiplier')}
        summaries={x['sku']:x for x in summary.get('products',[])}
        for item in m.get('products',[]):
            ap=bp.parent/'audits'/f"{item['sku']}.json"
            if ap.exists():sources.append((bp.parent,item,params,ap,summaries.get(item['sku'],{})))
    cache={}
    for base,item,params,ap,report in sources:
        try:
            if not ap.resolve().is_relative_to(log_root):raise ValueError('审核文件不在允许的日志目录')
            if not ap.is_file():raise ValueError('审核文件不存在')
            a=read(ap);erp=a.get('erp_fill_audit',{});rb=erp.get('product_library_readback',{})
            candidates=[];save_success=saved(erp)
            if valid_readback(rb,item,params['walmart_account']):candidates.append((ap,rb))
            if base not in cache:
                patches=[]
                for fn in ['save-recovery.json','unsaved-template-recovery.json','targeted-price-retries.json','targeted-part-number-repairs.json',
                           'final-persisted-readback.json','readback-recovery.json']:
                    fp=base/fn
                    if fp.is_file():patches.extend((fp,x) for x in objects(read(fp)) if isinstance(x,dict))
                cache[base]=patches
            for fp,x in cache[base]:
                if x.get('sku')!=item['sku'] or not same_template(x.get('template'),item):continue
                patch_rb=x.get('verification') or x.get('readback') or {}
                if not valid_readback(patch_rb,item,params['walmart_account']):continue
                if x.get('runtime_parameters') and x['runtime_parameters']!=params:continue
                if x.get('source_audit_sha256') and x['source_audit_sha256']!=hashlib.sha256(ap.read_bytes()).hexdigest():
                    warnings.append(dict(sku=item['sku'],file=fp.name,error='修订来源哈希不匹配，跳过该修订'));continue
                candidates.append((fp,patch_rb))
                if x.get('saved') is True or x.get('save_succeeded') is True or saved(x.get('original_save',{})):
                    save_success=True
            fallback=[]
            for fp,x in cache[base]:
                initial=x.get('initial_persisted_state',{})
                if (x.get('sku')==item['sku'] and same_template(x.get('template'),item)
                    and x.get('runtime_parameters')==params
                    and initial.get('fields',{}).get('templetName')==item['template']
                    and x.get('source_audit_sha256')==hashlib.sha256(ap.read_bytes()).hexdigest()):
                    fallback.append((fp,dict(state=initial,snapshot_origin='initial_persisted_state')))
            if candidates:
                ep,rb=max(candidates,key=lambda c:c[0].stat().st_mtime)
                kind='persisted_readback'
            elif fallback:
                ep,rb=max(fallback,key=lambda c:c[0].stat().st_mtime);kind='editor_snapshot'
            else:ep=ap;rb={};kind='editor_snapshot'
            complete=report.get('fill_complete', report.get('verified',False))
            issue_list=report.get('issues',[])
            if not issue_list and kind=='persisted_readback':
                issue_list=[{'section':'required',**f} for f in rb.get('required_fields',{}).get('incomplete',[]) if isinstance(f,dict)]
            listing=normalize(a,item,params,rb,kind,ep,timestamp(ep),save_success,complete,issue_list)
            # Avoid absolute filesystem exposure in browser/export; file is still hash traceable.
            listing.evidence['relative_file']=str(ep.relative_to(log_root))
            key=listing.external_id;old=selected.get(key)
            score=lambda x:(x.evidence_kind=='persisted_readback',datetime.fromisoformat(x.observed_at))
            if not old or score(listing)>score(old):selected[key]=listing
        except (ValueError,TypeError,KeyError,OSError) as exc:
            warnings.append(dict(sku=item.get('sku',''),file=ap.name,error=str(exc)))
    # Pre-existing uploaded products can have a read-only inspection but no fill
    # audit. Import their actual state, leaving save and price-source claims false.
    for fp in log_root.glob('*/existing-before-inspection.json'):
        mp=fp.parent/'existing-manifest.json'
        if not mp.exists():continue
        manifest=read(mp)
        if not all(k in manifest for k in ('brand','walmart_account','price_multiplier')):continue
        params={k:str(manifest[k]) for k in ('brand','walmart_account','price_multiplier')}
        items={x['sku']:x for x in manifest.get('products',[])}
        for inspection in objects(read(fp)):
            item=items.get(inspection.get('sku'))
            if not item:continue
            state=inspection.get('state',{})
            rb=dict(state=state,template=inspection.get('template'),reopened_on_new_page=True,
                    status='manual_review',required_fields=inspection.get('required',{}))
            if inspection.get('read_only') is not True or not valid_readback(rb,item,params['walmart_account']):
                warnings.append(dict(sku=item['sku'],file=fp.name,error='只读检查身份不匹配'));continue
            source_url=inspection.get('source_url','');asin=asin_from_url(source_url)
            if not asin:continue
            pseudo=dict(source=dict(sku=item['sku'],asin=asin,url=source_url),runtime_parameters=params)
            listing=normalize(pseudo,item,params,rb,'persisted_readback',fp,timestamp(fp),False,False,
                [dict(section='source',reason='已有商品的只读回读；缺少原填写审计和来源价格依据，未宣称双保存及完整性通过')])
            listing.evidence['relative_file']=str(fp.relative_to(log_root))
            listing.evidence['note']='原有模板只读检查，缺少最初填写来源；未重新计算价格或库存。'
            if listing.external_id not in selected:selected[listing.external_id]=listing
    return ImportBundle(namespace='digitemperor',label='数字酋长历史商品记录',listings=list(selected.values())),dict(
        selected=len(selected),skipped_unattempted=skipped_unattempted,warnings=warnings)
