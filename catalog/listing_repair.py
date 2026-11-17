"""Bounded field patches, driven by actual validation errors and provenance."""
import copy
import re

from .listing_ai_client import ListingAIError
from .listing_generation import (PROTECTED,CONTENT_FIELDS,BLOCKED,RULES,empty,candidate_value,
    fact_reference_exists,summarize_generation_issues,semantic_patch_error)
from .walmart_listing_schema import sections

MAX_REPAIR_ROUNDS=3
FORBIDDEN=PROTECTED | {'brand','mainImageUrl','productSecondaryImageURL'}


def field_path(path):
    normalized=summarize_generation_issues([dict(path=path,message='')])[0]['path']
    parts=normalized.split('.')
    return '.'.join(parts[:2]) if len(parts)>1 and parts[0] in ('visible','orderable') else ''


def repair_fields(row,schema,issues,audit):
    spec=sections(schema,row['product_type']);fields={}
    roots={field_path(issue['path']) for issue in issues}
    # Only controllers belonging to a branch that requires the failed target.
    def required_targets(branch):
        if not isinstance(branch,dict):
            return set()
        keys=set(branch.get('required',[]))
        for key in ('allOf','anyOf','oneOf'):
            for child in branch.get(key,[]):
                keys.update(required_targets(child))
        return keys
    targets={p.split('.',1)[1] for p in roots if p.startswith('visible.')}
    def controllers(shape):
        if not isinstance(shape,dict):
            return set()
        result=set()
        if targets & (required_targets(shape.get('then',{})) | required_targets(shape.get('else',{}))):
            result.update(shape.get('if',{}).get('properties',{}))
        for key in ('allOf','anyOf','oneOf'):
            for child in shape.get(key,[]):
                result.update(controllers(child))
        return result
    for key in controllers(spec['visible']):
        path='visible.'+key;evidence=audit.get(path,{})
        if not evidence.get('manual_locked') and evidence.get('value')==row['visible'].get(key) and evidence.get('kind') in ('source','ai_source','estimate','visual_inference'):
            roots.add(path)
    for path in roots:
        if not path:
            continue
        section,key=path.split('.',1)
        props=spec[section].get('properties',{})
        if key in FORBIDDEN or key not in props or BLOCKED.search(key):
            continue
        current=row[section].get(key);previous=audit.get(path,{})
        if previous.get('manual_locked'):
            continue
        if not empty(current) and (previous.get('value')!=current or previous.get('kind') not in (
                'source','ai_source','estimate','visual_inference','image_label')):
            continue
        fields[path]=props[key]
    return fields


def valid_evidence(action,facts):
    refs=action.get('refs',[]);confidence=action.get('confidence');kind=action.get('kind')
    if (not isinstance(refs,list) or any(not fact_reference_exists(facts,r) for r in refs)
            or type(confidence) not in (int,float) or not 0<confidence<=1
            or kind not in ('source','estimate') or not isinstance(action.get('reason'),str) or not action['reason'].strip()
            or kind=='source' and (confidence<=.75 or not refs)):
        return False
    if kind=='source' and any(r.startswith('visual_evidence') for r in refs):
        # Visual guesses cannot be laundered into supplier facts.
        return False
    return True


def repair_row(row,schema,issues,facts,ai,audit,round_number):
    fields=repair_fields(row,schema,issues,audit)
    if not fields:
        return row,dict(state='not_repairable',fields=[],issues=issues)
    proposal=ai.query('repair_attributes',dict(source_sku=row['source_sku'],product_type=row['product_type'],
        existing=row,facts=facts,fields=fields,errors=summarize_generation_issues(issues),rules=RULES,round=round_number,
        instructions='Repair only listed fields using source facts, visual evidence or defensible ordinary estimates. '
            'Do not delete fields or change identity, category, price, inventory, images or real identifiers. '
            'Do not remove a real Sleeper design just to avoid bedSize. Modular and Sleeper may coexist. '
            'Do not select Non-Standard merely because bed dimensions are unknown. Preserve all manual values.',
        output_contract={'source_sku':row['source_sku'],'actions':[{'field':'exact allowed path','value':'valid schema value',
            'kind':'source or estimate','refs':['real fact path'],'confidence':'0..1','reason':'中文依据与修复理由'}],
            'missing':[{'field':'allowed path','reason':'中文实际障碍'}]}))
    if proposal.get('source_sku')!=row['source_sku'] or not isinstance(proposal.get('actions'),list):
        raise ListingAIError('修复计划 SKU 或格式不匹配，未写入')
    accepted=[];seen=set();rejected=[]
    for action in proposal['actions']:
        path=action.get('field') if isinstance(action,dict) else None
        if path not in fields or path in seen:
            raise ListingAIError('修复计划包含越权或重复字段，整轮未采用')
        seen.add(path)
        value=candidate_value(fields[path],action.get('value'))
        if empty(value) or not valid_evidence(action,facts):
            rejected.append(dict(path=path,message='修复值或依据未通过校验'))
            continue
        section,key=path.split('.',1)
        error=semantic_patch_error(path,value,facts,row[section].get(key))
        if error:
            rejected.append(dict(path=path,message=error))
            continue
        accepted.append((path,value,action))
    result=copy.deepcopy(row);changes=[]
    for path,value,action in accepted:
        section,key=path.split('.',1);previous=result[section].get(key)
        if value==previous:
            continue
        result[section][key]=value
        audit[path]=action | dict(value=value,previous_value=previous,round=round_number)
        if action['kind']=='estimate' and any(r.startswith('visual_evidence') for r in action.get('refs',[])):
            audit[path]['kind']='visual_inference'
        changes.append(dict(path=path,before=previous,after=value))
    return result,dict(state='changed' if changes else 'no_progress',changes=changes,rejected=rejected,
        missing=proposal.get('missing',[]),fields=list(fields),issues=issues)


def prerequisite_issue(issue):
    path=issue.get('path','')
    return (path in ('identification','fulfillment_center_id') or 'productIdentifiers' in path
            or path in ('orderable.price','orderable.msrp','orderable.inventory')
            or not path and any(word in issue.get('message','') for word in ('来源','库存','售价','过期','授权','黑名单')))
