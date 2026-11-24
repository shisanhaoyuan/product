import copy

import pytest

from catalog.listing_generation import fill_row
from catalog.listing_part_number import part_number_field
from catalog.walmart_listing_schema import sections
from test_listing_generation import AI, listing
from test_walmart_publishing import spec, PT


def shape(key='manufacturerPartNumber', **limits):
    schema = spec()
    visible = sections(schema, PT)['visible']
    visible['properties'][key] = dict(type='string', minLength=1, maxLength=100, **limits)
    visible['required'].append(key)
    return schema


class NameAI(AI):
    def __init__(self, value='Small Bathroom Storage Cabinet', **changes):
        super().__init__()
        self.value = value
        self.changes = changes

    def query(self, purpose, payload):
        if purpose == 'skill_part_number':
            self.calls.append((purpose, payload))
            return dict(source_sku=payload['source_sku'], value=self.value,
                        refs=['content.copy.title'], reason='按同 SKU 标题概括商品名称') | self.changes
        return super().query(purpose, payload)


@pytest.mark.parametrize('key,title', [('manufacturerPartNumber',''), ('part_number',''), ('MPN',''),
                                     ('customMpn','Manufacturer Part Number')])
def test_part_field_aliases(key,title):
    assert part_number_field(key, {'title':title})
    assert not part_number_field('modelNumber', {})


@pytest.mark.parametrize('facts,ref', [
    ({'content.attributes':{'Recommended Uses For Product':'  bathroom storage  '}},
     'content.attributes.Recommended Uses For Product'),
    ({'detail.attributes':{'Recommended Uses For Product':'bathroom storage'}},
     'detail.attributes.Recommended Uses For Product'),
    ({'verified_source':{'raw_attributes':{'Recommended Uses For Product':'bathroom storage'}}},
     'verified_source.raw_attributes.Recommended Uses For Product'),
    ({'detail.ProductAttributeList':[{'AttributeNameEn':'Recommended Uses For Product','AttributeValue':'bathroom storage'}]},
     'detail.ProductAttributeList.0.AttributeValue'),
])
def test_uses_value_keeps_case_and_is_user_rule_not_real_mpn(facts,ref):
    ai = NameAI(); audit = {}; row = listing(); before = copy.deepcopy(row)
    result, issues = fill_row(row,shape(),facts,ai,audit)
    assert result['visible']['manufacturerPartNumber'] == 'bathroom storage'
    assert not any(purpose=='skill_part_number' for purpose,_ in ai.calls)
    assert audit['visible.manufacturerPartNumber']['kind'] == 'user_rule'
    assert audit['visible.manufacturerPartNumber']['refs'] == [ref]
    assert '厂家真实零件编号' in audit['visible.manufacturerPartNumber']['reason']
    assert not any(i['path']=='visible.manufacturerPartNumber' for i in issues)
    assert row == before and result['orderable'] == row['orderable']
    repeated, _ = fill_row(result,shape(),facts,NameAI(),{})
    assert repeated == result


@pytest.mark.parametrize('placeholder', [None, '', 'N/A', '--', 'Not Available'])
def test_missing_uses_summarizes_same_product_once_and_records_rule(placeholder):
    facts = {'content.attributes':{'Recommended Uses For Product':placeholder},
             'content.copy':{'title':'Small bathroom cabinet with doors'}, 'detail.mpn':'Factory-123'}
    ai = NameAI(); audit = {}
    result, issues = fill_row(listing(),shape(),facts,ai,audit)
    assert result['visible']['manufacturerPartNumber'] == 'Small Bathroom Storage Cabinet'
    assert audit['visible.manufacturerPartNumber']['kind'] == 'user_rule'
    assert audit['visible.manufacturerPartNumber']['refs'] == ['content.copy.title']
    assert len([1 for purpose,_ in ai.calls if purpose=='skill_part_number']) == 1
    assert not any(i['path']=='visible.manufacturerPartNumber' for i in issues)


def test_original_uses_case_length_complete_words_and_official_limit():
    facts = {'content.attributes':{'Recommended Uses For Product':
        'Bathroom Storage And Organization For Everyday Household Supplies And Accessories'}}
    ai = NameAI()
    result, _ = fill_row(listing(),shape(),facts,ai,{})
    assert result['visible']['manufacturerPartNumber'] == 'Bathroom Storage And Organization For Everyday Household'
    schema = shape(); sections(schema,PT)['visible']['properties']['manufacturerPartNumber']['maxLength']=20
    result, _ = fill_row(listing(),schema,facts,ai,{})
    assert result['visible']['manufacturerPartNumber'] == 'Bathroom Storage And'
    assert not any(purpose=='skill_part_number' for purpose,_ in ai.calls)


@pytest.mark.parametrize('value,changes', [
    ('W1', {}), ('MY171234AB', {}), ('NewBrand Storage Cabinet', {}), ('012345678905', {}),
    ('B012345678', {}), ('Factory-123', {}), ('', {}),
    ('Storage Cabinet', {'source_sku':'OTHER'}),
    ('Storage Cabinet', {'refs':['content.copy.not_real']}),
])
def test_no_identifier_brand_cross_sku_or_fabricated_source_fallback(value,changes):
    ai = NameAI(value, **changes)
    result, issues = fill_row(listing(),shape(),{'content.copy':{'title':'Bathroom cabinet'},
                                              'detail.mpn':'Factory-123'},ai,{})
    assert not result['visible'].get('manufacturerPartNumber')
    assert len([i for i in issues if i['path']=='visible.manufacturerPartNumber']) == 1
    assert len([1 for purpose,_ in ai.calls if purpose=='skill_part_number']) == 1


def test_manual_mpn_and_other_protected_fields_are_preserved():
    row = listing(); row['visible']['manufacturerPartNumber']='Manually Verified MPN'
    row['orderable']['ShippingWeight']=10
    schema = shape(); visible = sections(schema,PT)['visible']
    visible['properties']['modelNumber']={'type':'string'}; visible['required'].append('modelNumber')
    ai = NameAI(); audit = {}
    result, issues = fill_row(row,schema,{'content.copy':{'title':'Cabinet'}},ai,audit)
    assert result['visible']['manufacturerPartNumber'] == 'Manually Verified MPN'
    assert 'modelNumber' not in result['visible']
    assert any(i['path']=='visible.modelNumber' and '不能估算' in i['message'] for i in issues)
    assert not any(purpose=='skill_part_number' for purpose,_ in ai.calls)
    for key in ('source_sku','platform_sku','identification','orderable'):
        assert result[key] == row[key]
