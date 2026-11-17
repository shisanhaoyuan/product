import copy
import json
from pathlib import Path

import pytest

from catalog.listing_ai_client import ListingAIError
from catalog.listing_generation import candidate_value, fill_row
from catalog.listing_skill_defaults import skill_default, length_fallback
from catalog.walmart_listing_schema import precise_validator, sections, schema_errors, feed_item, DEFAULT_VERSION
from test_listing_generation import AI, listing
from test_walmart_publishing import spec, PT


class Offline(AI):
    def query(self, purpose, payload):
        self.calls.append((purpose, payload))
        raise ListingAIError('AI 请求失败（HTTP 503）')


def test_real_sofa_schema_defaults_fallback_copy_and_untouched_business_fields():
    schema = json.loads((Path(__file__).parent/'fixtures/walmart/sofas-mp-item-20260829.json').read_text())
    row = listing()
    row['product_type'] = 'Sofas & Couches'
    row['orderable'].update(price=323.9, msrp=647.8, ShippingWeight=99)
    row['visible'].update(shortDescription='<p> </p>', recommendedLocations=[''],
        frameMaterial=['Old class frame material'],
        assembledProductLength={'measure':103.2,'unit':'in'},
        assembledProductWidth={'measure':67.8,'unit':'in'})
    features = ['Modular sectional sofa with a corduroy fabric cover and foam filling.',
                'No assembly required. The sectional has three seats and a black fabric cover.']
    facts = {'content.copy':{'title':'Black sectional sofa','features':features},
             'content.attributes':{'Main Color':'Black','Main Material':'Fabric'}}
    before = copy.deepcopy(row);audit = {};ai=Offline()
    result, issues = fill_row(row,schema,facts,ai,audit)
    visible = result['visible']
    assert visible['has_written_warranty']=='No'
    assert visible['isProp65WarningRequired']=='No'
    assert visible['isAssemblyRequired']=='No'
    assert visible['ageGroup']==['Adult']
    assert visible['smallPartsWarnings']==['0 - No warning applicable']
    assert visible['netContent']=={'productNetContentUnit':'Each','productNetContentMeasure':1}
    assert visible['assembledProductHeight']==visible['seat_depth']=={'measure':22,'unit':'in'}
    assert 'Main Color: Black' in visible['shortDescription'] and 'Main Material: Fabric' in visible['shortDescription']
    assert all(feature not in visible['shortDescription'] for feature in visible['keyFeatures'])
    assert features[0] not in visible['shortDescription']
    assert audit['visible.shortDescription']['kind']=='user_rule'
    assert 'previous_value' in audit['visible.shortDescription']
    assert audit['visible.assembledProductHeight']['kind']=='user_rule'
    assert audit['visible.frameMaterial']['previous_value']==['Old class frame material']
    assert 'frameMaterial' not in visible and 'recommendedLocations' not in visible
    for key in ('assembledProductLength','assembledProductWidth','keyFeatures','mainImageUrl','brand','productName'):
        assert visible[key]==before['visible'][key]
    for key,value in before['orderable'].items():
        assert result['orderable'][key]==value
    assert result['orderable']['country_of_origin_substantial_transformation']=='China'
    assert result['orderable']['fulfillmentLagTime']==1
    for key in ('source_sku','platform_sku','listing_id','identification'):
        assert result[key]==before[key]
    assert row==before
    assert not any(p['path'] in ('visible.assembledProductHeight','visible.seat_depth') for p in issues)
    for path in ('visible.has_written_warranty','visible.isProp65WarningRequired','visible.ageGroup'):
        assert audit[path]['kind']=='user_rule' and 'confidence' not in audit[path]
    item,validation = feed_item(result,schema,DEFAULT_VERSION,'',stock=10)
    errors = schema_errors(validation,item,DEFAULT_VERSION)
    assert not any('multipleOf' in e['message'] for e in errors)
    assert all(field in visible for field in ('has_written_warranty','netContent','ageGroup'))
    # Re-running cannot reapply prices, duplicate copy or change existing defaults.
    repeated,_ = fill_row(result,schema,facts,Offline(),{})
    assert repeated==result


@pytest.mark.parametrize('key,value',[
    ('isFragile','No'),('isProp65WarningRequired','No'),('hasNRTLListingCertification','No'),
    ('has_written_warranty','No'),('condition','New'),('isAssemblyRequired','No'),
    ('country_of_origin_substantial_transformation','China'),('countryOfOriginTextiles','Imported'),
    ('stateChemicalDisclosure','无'),('adjustableHeight','No'),('powerSource','无'),
])
def test_defaults_only_fill_missing_real_fields_and_real_options(key,value):
    field = {'type':'string','enum':[value,'Existing']}
    filled,reason = skill_default(key,field,'',{})
    assert filled==value and 'user_authorized_rule' in reason
    assert skill_default(key,field,'Existing',{})[0] is None
    assert skill_default(key,{'type':'string','enum':['Another']},None,{})[0] is None
    assert skill_default('unknown',field,None,{})[0] is None


@pytest.mark.parametrize('code', ['0 - No warning applicable','0',0])
def test_warning_codes_follow_actual_schema_representation(code):
    field = {'type':'array','items':{'type':'integer' if code==0 else 'string','enum':[code]}}
    assert skill_default('smallPartsWarnings',field,None,{})[0]==[code]


def test_all_age_defaults_require_exact_evidence_and_atomic_options():
    field={'type':'array','items':{'type':'string','enum':['Adult','Teen','Child','Adult,Teen','','请选择']}}
    assert skill_default('ageGroup',field,None,{'content.copy':{'features':['Suitable for all ages.']}})[0]==['Adult','Teen','Child']
    assert skill_default('ageGroup',field,None,{'content.copy':{'features':['People of all ages love it.']}})[0]==['Adult']
    assert skill_default('fulfillmentLagTime',{'type':'integer'},None,{})[0]==1


@pytest.mark.parametrize('units,current,expected',[
    (['Each','Case'],None,{'productNetContentUnit':'Each','productNetContentMeasure':1}),
    (['Case'],{'productNetContentUnit':'Each','productNetContentMeasure':7},
        {'productNetContentUnit':'Case','productNetContentMeasure':7}),
    (['Case'],{'productNetContentUnit':'请选择','productNetContentMeasure':3},
        {'productNetContentUnit':'Case','productNetContentMeasure':3}),
    (['Case','Pound'],{'productNetContentUnit':'Pound','productNetContentMeasure':4.5},
        {'productNetContentUnit':'Pound','productNetContentMeasure':4.5}),
    (['Pound'],None,None),
    (['Each','Case'],{'productNetContentUnit':'Other','productNetContentMeasure':8},None),
])
def test_net_content_real_units_and_preserved_quantities(units,current,expected):
    field={'type':'object','properties':{'productNetContentUnit':{'type':'string','enum':units},
        'productNetContentMeasure':{'type':'number'}},'required':['productNetContentUnit','productNetContentMeasure']}
    assert skill_default('netContent',field,current,{})[0]==expected
    assert skill_default('shippingWeight',field,None,{})[0] is None


def test_source_wins_and_existing_partial_values_legal_yes_and_ids_are_protected():
    row=listing();row['visible'].update(isProp65WarningRequired='Yes',
        assembledProductHeight={'measure':38,'unit':'in'})
    schema=spec();props=sections(schema,PT)['visible']['properties']
    props['countryOfOriginSubstantialTransformation']={'type':'string','enum':['China','Vietnam']}
    props['netContent']={'type':'object','properties':{'unit':{'enum':['Case']},'measure':{'type':'number'}},'required':['unit','measure']}
    row['visible']['netContent']={'unit':'Each','measure':8}
    facts={'detail.placeOfOrigin':'Vietnam','content.copy':{'title':'Chair'}}
    audit={};result,issues=fill_row(row,schema,facts,Offline(),audit)
    assert result['visible']['countryOfOriginSubstantialTransformation']=='Vietnam'
    assert result['visible']['isProp65WarningRequired']=='Yes'
    assert result['visible']['assembledProductHeight']==row['visible']['assembledProductHeight']
    assert result['visible']['netContent']=={'unit':'Case','measure':8}
    assert any(i['path']=='visible.prop65WarningText' for i in issues)
    assert result['identification']==row['identification']
    assert result['orderable']==row['orderable']
    dims=props['assembledProductHeight']
    assert length_fallback('assembledProductHeight',dims,{'measure':19}) is None
    assert length_fallback('assembledProductWeight',dims,None) is None
    assert length_fallback('assembledProductHeight',dims,{'unit':'ft'}) is None
    assert length_fallback('assembledProductHeight',dims,{'unit':'in'})=={'unit':'in','measure':22}


def test_missing_copy_is_sku_scoped_and_insufficient_facts_are_not_duplicated():
    row=listing();row['visible'].update(shortDescription='',keyFeatures=[])
    other=copy.deepcopy(row);other['source_sku']='Other'
    facts={'content.copy':{'features':['Blue fabric chair.']}}
    one,_=fill_row(row,spec(),facts,Offline(),{})
    two,_=fill_row(other,spec(),{'content.copy':{'features':['Red wood chair.']}},Offline(),{})
    assert 'Red' not in one['visible'].get('shortDescription','')
    assert 'Blue' not in two['visible'].get('shortDescription','')
    assert not one['visible'].get('shortDescription')
    assert one['visible']['keyFeatures']==['Blue fabric chair.']
    shape=spec();sections(shape,PT)['visible']['properties']['shortDescription']['minLength']=500
    incomplete,_=fill_row(row,shape,facts,Offline(),{})
    assert 'shortDescription' not in incomplete['visible']


def test_decimal_precision_survives_nested_dialect_redeclarations():
    # The actual official schema repeats $schema dozens of times.
    dialect='http://json-schema.org/draft-07/schema#'
    schema={'$schema':dialect,'type':'object','properties':{'price':{'$schema':dialect,'type':'number','multipleOf':.01}}}
    assert precise_validator(schema).is_valid({'price':323.9})
    assert precise_validator(schema).is_valid({'price':647.8})
    assert not precise_validator(schema).is_valid({'price':323.901})
    assert not precise_validator(schema).is_valid({'price':True})
    assert candidate_value(schema,{'price':323.9})=={'price':323.9}
    assert candidate_value(schema,{'price':323.901}) is None


def test_known_source_height_with_decimal_precision_wins_over_skill_fallback():
    schema=spec()
    height=sections(schema,PT)['visible']['properties']['assembledProductHeight']
    height['$schema']='http://json-schema.org/draft-07/schema#'
    height['properties']['measure'].update(multipleOf=.01)
    facts={'detail.assembledHeight':'32.30','detail.assembledLengthUnit':'inches'}
    audit={};result,_=fill_row(listing(),schema,facts,Offline(),audit)
    assert result['visible']['assembledProductHeight']=={'measure':32.3,'unit':'in'}
    assert audit['visible.assembledProductHeight']['kind']=='source'
