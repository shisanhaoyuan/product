"""Read-only, same-SKU supplements; prices/inventory and catalog edits stay frozen."""
import copy
import json
import re

from .listing_generation import empty, source_facts
from .listing_ai_client import ListingAIError
from .prelisting import fingerprint


def amazon_details(html,asin):
    from .images import needs_verification, HumanVerificationRequired, ProductHTML
    from .source_capture import Document
    if needs_verification(html):
        raise HumanVerificationRequired('来源需要人工验证')
    identity=ProductHTML();identity.feed(html)
    if identity.asins!={asin}:
        raise ListingAIError('补采页面 ASIN 与当前子商品不一致')
    root=Document(html).root
    title=root.ids('productTitle')
    features=[n.text() for section in root.ids('feature-bullets') for n in section.nodes()
              if n.tag=='li' and n.text()]
    description=root.ids('productDescription')
    attrs={}
    for section in root.ids('productOverview_feature_div','productDetails_techSpec_section_1','productDetails_detailBullets_sections1'):
        for tr in (n for n in section.nodes() if n.tag=='tr'):
            labels=[n.text() for n in tr.nodes() if n.tag=='th']
            values=[n.text() for n in tr.nodes() if n.tag=='td']
            if len(labels)==len(values)==1 and labels[0] and values[0]:
                attrs[labels[0]]=values[0]
    return {'content.copy':dict(title=title[0].text() if len(title)==1 else '',
            features=list(dict.fromkeys(features)),description=description[0].text() if len(description)==1 else ''),
            'content.attributes':attrs}


def supplement_facts(catalog,source,item,variant,ai,amazon_details_data=None):
    facts=source_facts(source,item,variant)
    if not getattr(ai.settings,'supplement',False) and amazon_details_data is None:
        return facts,dict(state='disabled')
    from .images import HumanVerificationRequired,fetch_amazon_page
    try:
        if source['source'] in ('GigaB2B', 'GigaB2B_CA'):
            from .giga import GigaSettings,GigaCanadaSettings,GigaClient,indexed
            settings=GigaCanadaSettings() if source['source']=='GigaB2B_CA' else GigaSettings()
            with catalog.connect() as db:
                stored=db.execute('SELECT namespace FROM listings WHERE id=?',(item['listing_id'],)).fetchone()
            if not settings.configured or not stored or stored['namespace']!=settings.namespace:
                return facts,dict(state='unavailable',message='当前云仓 API 账号与该来源不一致或未配置，使用已核验快照')
            detail=indexed(GigaClient(settings).query('detail',{'skus':[variant['sku']]}),[variant['sku']],'补采详情')[variant['sku']]
            supplemented=copy.deepcopy(source)
            supplemented.setdefault('source_facts',{}).setdefault('giga',{})['detail']=detail
            collected=source_facts(supplemented,item,variant)
        elif source['source']=='Saleyee':
            from .saleyee import SaleyeeSettings,SaleyeeClient,index_rows
            settings=SaleyeeSettings()
            with catalog.connect() as db:
                stored=db.execute('SELECT namespace FROM listings WHERE id=?',(item['listing_id'],)).fetchone()
            if not settings.configured or not stored or stored['namespace']!=settings.namespace:
                return facts,dict(state='unavailable',message='当前赛盈 API 账号与该来源不一致或未配置，使用已有快照')
            result=SaleyeeClient(settings).query('detail',{'Skus':[variant['sku']],'PageIndex':1,'PageSize':30})
            detail=index_rows(result['ProductInfoList'],[variant['sku']])[variant['sku']]
            supplemented=copy.deepcopy(source)
            supplemented.setdefault('source_facts',{}).setdefault('saleyee',{})['detail']=detail
            collected=source_facts(supplemented,item,variant)
        elif source['source'].casefold()=='amazon':
            asin=variant.get('asin','')
            if not re.fullmatch('[A-Z0-9]{10}',asin):
                return facts,dict(state='unavailable',message='当前 SKU 缺少明确 ASIN，未猜测来源链接')
            collected=amazon_details_data if amazon_details_data is not None else amazon_details(fetch_amazon_page(asin),asin)
        else:
            return facts,dict(state='unavailable',message='该来源暂未配置定向补采适配器')
        conflicts=[]
        copy_differences=[]
        def merge(old,new,path):
            if isinstance(old,dict) and isinstance(new,dict):
                result=copy.deepcopy(old)
                for key,value in new.items():
                    result[key]=merge(old.get(key),value,path+'.'+key)
                return result
            if empty(old):
                return new
            if not empty(new) and old!=new:
                if source['source'].casefold() == 'amazon' and path.startswith('.content.copy.'):
                    # ERP copy is deliberately rewritten for the target brand/store.
                    # Keep it and expose live copy separately; it is not an identity conflict.
                    copy_differences.append(path)
                else:
                    conflicts.append(path)
            return old
        result=merge(facts,collected,'')
        if copy_differences:
            result['amazon_live_copy'] = copy.deepcopy(collected.get('content.copy', {}))
        # Record supplemental facts independently; do not silently rebase prices,
        # source timestamps or the existing prelisting business snapshot.
        return result,dict(state='conflict' if conflicts else 'complete',conflicts=conflicts[:40],
            copy_differences=copy_differences[:40],
            digest=fingerprint(collected),facts=collected,
            message=('补采规格与已有来源不同：' + '、'.join(p.removeprefix('.content.attributes.') for p in conflicts[:10]) + '；请核对这些字段后刷新来源'
                     if conflicts else '同 ASIN 文案存在改写差异，已保留商品库文案并附上来源原文；不影响图片核验和生成'
                     if copy_differences else '同 SKU 补采完成；价格库存仍按冻结快照校验'))
    except HumanVerificationRequired:
        return facts,dict(state='verification_required',message='来源需要人工验证，请在商品档案完成来源采集后重新生成',
            listing_id=item['listing_id'],source_sku=variant['sku'],asin=variant.get('asin',''))
    except Exception:
        return facts,dict(state='failed',message='该 SKU 来源补采失败，保留已核验来源及现有售价库存')
