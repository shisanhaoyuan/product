import copy
import json

import pytest

from catalog.canada_tax_codes import library, search, fill_tax_code, errors, unavailable
from catalog.listing_ai_client import ListingAIError
from catalog.listing_generation import fill_row, source_facts
from catalog.walmart_canada import official_bundle
from test_listing_generation import listing, AI
from test_walmart_canada_publishing import ca_publishing, canada, vault, settings


class TaxAI(AI):
    def __init__(self, code=2040175):
        super().__init__()
        self.code = code

    def query(self, purpose, payload):
        if purpose == 'canada_tax_group':
            self.calls.append((purpose, payload))
            path = library()['entries'][self.code]['path']
            gid = next(g['id'] for g in payload['groups'] if g['path'] == path)
            return dict(source_sku=payload['source_sku'], group_id=gid)
        if purpose == 'canada_tax_code':
            self.calls.append((purpose, payload))
            return dict(source_sku=payload['source_sku'], code=self.code,
                        refs=['content.copy.title'], confidence=.96, reason='商品为家具，匹配表中的 Furniture')
        return super().query(purpose, payload)


def ca_row():
    row = listing()
    row['product_type'] = 'Furniture'
    return row


def test_imported_codes_complete_and_traceable():
    data = library()
    assert len(data['entries']) == data['source']['count'] == 8227
    assert data['source']['version'] == '2026-05'
    entry = data['entries'][2040175]
    assert entry['description'] == 'Furniture' and entry['source_row'] == 1236
    assert search(code='2040175')['items'][0]['code'] == 2040175
    assert search('furniture')['total'] > 0
    assert search('nonsense keyword')['items'] == []
    assert search(offset=50)['items'][0]['code'] != search()['items'][0]['code']
    assert not unavailable(dict(description='(Reserved for Canada) Food'))
    assert unavailable(dict(description='Reserved – Previously applied to goods'))


@pytest.mark.parametrize('source', ['Amazon_CA', 'GigaB2B_CA'])
def test_all_canadian_sources_classify_from_same_code_library(source):
    row = ca_row(); schema = official_bundle()['specs']['Furniture']; original = copy.deepcopy(schema)
    variant = dict(sku=row['source_sku'], asin='B000000001', content={'copy': {'title': 'Wooden bed frame'}})
    product = dict(source=source, sku=row['source_sku'], variants=[dict(sku=row['source_sku'], asin='B000000001')],
                   source_facts={'giga': {'detail': {'sku': row['source_sku'], 'productName': 'Wooden bed frame'}}})
    facts = source_facts(product, {'category': 'Beds'}, variant)
    ai = TaxAI(); audit = {}
    filled, issues = fill_row(row, schema, facts, ai, audit)
    assert filled['orderable']['productTaxCode'] == 2040175
    assert not any(i['path'] == 'orderable.productTaxCode' for i in issues)
    assert 'productTaxCode' not in row['orderable']  # No mutation of caller/shared schema.
    assert schema == original
    evidence = audit['orderable.productTaxCode']
    assert evidence['matrix_version'] == '2026-05' and evidence['source_row'] == 1236
    assert evidence['kind'] == 'ai_source' and evidence['description'] == 'Furniture'
    assert [purpose for purpose, _ in ai.calls if purpose.startswith('canada_tax')] == ['canada_tax_group', 'canada_tax_code']
    for purpose, payload in ai.calls:
        if purpose == 'required_attributes':
            assert 'orderable.productTaxCode' not in payload['fields']


def test_manual_and_source_codes_preserved_without_ai():
    row = ca_row(); schema = official_bundle()['specs']['Furniture']
    class NoAI:
        def query(self, *args):
            raise AssertionError('No AI needed')
    row['orderable']['productTaxCode'] = 2038710
    assert not fill_tax_code(row, schema, {}, NoAI(), {})
    assert row['orderable']['productTaxCode'] == 2038710
    row['orderable'].pop('productTaxCode')
    audit = {}
    assert not fill_tax_code(row, schema, {'content.attributes': {'productTaxCode': 2040175}}, NoAI(), audit)
    assert row['orderable']['productTaxCode'] == 2040175
    assert audit['orderable.productTaxCode']['kind'] == 'source'


@pytest.mark.parametrize('fault', ['sku', 'code', 'other_group', 'refs', 'confidence', 'group', 'unavailable'])
def test_ai_cannot_invent_codes_or_cross_sku_or_group(fault):
    class Bad(TaxAI):
        def query(self, purpose, payload):
            if fault == 'unavailable':
                raise ListingAIError('测试 AI 不可用')
            result = super().query(purpose, payload)
            if purpose == 'canada_tax_group' and fault == 'group':
                result['group_id'] = []
            if purpose == 'canada_tax_code':
                if fault == 'sku': result['source_sku'] = 'another-sku'
                if fault == 'code': result['code'] = 9999999
                if fault == 'other_group': result['code'] = 2051871
                if fault == 'refs': result['refs'] = ['content.copy.invented']
                if fault == 'confidence': result['confidence'] = True
            return result
    row = ca_row(); audit = {}
    issues = fill_tax_code(row, official_bundle()['specs']['Furniture'],
                           {'content.copy': {'title': 'Wood chair'}}, Bad(), audit)
    assert issues and issues[0]['path'] == 'orderable.productTaxCode'
    assert 'productTaxCode' not in row['orderable'] and not audit


def test_invalid_manual_code_blocks_validation_without_replacement():
    row = ca_row(); row['orderable']['productTaxCode'] = 9999999
    schema = official_bundle()['specs']['Furniture']
    assert errors(row, schema)
    assert fill_tax_code(row, schema, {}, TaxAI(), {})
    assert row['orderable']['productTaxCode'] == 9999999
    assert errors(row, {k: v for k, v in schema.items() if k != '_ca'}) == []


def test_canadian_tax_search_endpoint_and_submission_validation(ca_publishing):
    c, _, _, base, _ = ca_publishing
    result = c.get(base + '/canada/tax-codes', params={'q': 'furniture'})
    assert result.status_code == 200, result.text
    assert any(e['code'] == 2040175 for e in result.json()['items'])
    assert c.get(base + '/canada/tax-codes', params={'code': '2040175'}).json()['items'][0]['source_row'] == 1236
    assert c.get(base + '/canada/tax-codes', params={'offset': -1}).status_code == 422
    assert c.get(base + '/canada/tax-codes', params={'limit': 101}).status_code == 422
    doc = c.get(base).json()
    c.post(base + '/nodes')
    for row in doc['rows']:
        row['product_type'] = 'Furniture'
        c.post(base + '/spec', json=row)
        row['orderable']['productTaxCode'] = 9999999
    saved = c.patch(base, json={k: doc[k] for k in ('revision', 'version', 'rows')}).json()
    checked = c.post(base + '/validate', json={'revision': saved['revision']}).json()
    for result in checked['validation']['rows'].values():
        assert any(i['path'] == 'orderable.productTaxCode' and '税码表' in i['message'] for i in result['errors'])


def test_us_does_not_use_canadian_tax_codes():
    from test_walmart_publishing import spec
    row = listing(); row['orderable']['productTaxCode'] = 9999999
    assert fill_tax_code(row, spec(), {}, TaxAI(), {}) == []
    assert errors(row, spec()) == []
