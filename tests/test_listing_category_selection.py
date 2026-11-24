"""Offline regressions for supplier wording vs the store's official taxonomy."""
import copy

import pytest

from catalog.listing_ai_client import ListingAIError
from catalog.listing_generation import choose_type


def option(name, group='Other', category='Other', **values):
    return dict(name=name, group=group, category=category, **values)


def source():
    row = dict(source_sku='82632470', product_type='', visible={})
    item = dict(category='Patio & Garden > Sunshade > Canopy')
    facts = dict(category=item['category'], **{'detail.EnName':
        '80*38in Household Application Door & Window Rain Cover Eaves Brown Board & Black Holder'})
    return row, item, facts


class AI:
    def __init__(self, *names):
        self.names = iter(names)
        self.calls = []

    def query(self, purpose, payload):
        self.calls.append((purpose, payload))
        return dict(source_sku=payload['source_sku'], product_type=next(self.names),
                    confidence=.98, refs=['detail.EnName'], reason='同 SKU 门窗雨棚的结构及用途')


def test_saleyee_door_cover_retrieves_official_awning_beyond_first_sixty():
    row, item, facts = source()
    types = [option(f'Household Window Board {i}', 'Patio & Garden') for i in range(90)]
    types += [option('Awnings', 'Outdoor Structures', 'Home Improvement')]
    ai = AI('Awnings')
    before = copy.deepcopy((row, item, facts, types))
    assert choose_type(row, item, facts, types, ai)[0] == 'Awnings'
    assert 'Awnings' in {v['name'] for v in ai.calls[0][1]['candidates']}
    assert len(ai.calls) == 1
    assert (row, item, facts, types) == before


def test_valid_official_answer_outside_shortlist_is_accepted():
    row, item, facts = source()
    types = [option(f'Window Rain Cover {i}') for i in range(90)] + [option('Building Accessories')]
    ai = AI('Building Accessories')
    assert choose_type(row, item, facts, types, ai)[0] == 'Building Accessories'
    assert 'Building Accessories' not in {v['name'] for v in ai.calls[0][1]['candidates']}
    assert len(ai.calls) == 1


@pytest.mark.parametrize('invalid', ['', 'Patio & Garden > Sunshade > Canopy', '雨棚', None])
def test_invalid_first_choice_recovers_from_complete_official_candidates_once(invalid):
    row, item, facts = source()
    types = [option(f'Window Rain Cover {i}', description='x' * 2000) for i in range(90)]
    types += [option('Building Accessories')]
    ai = AI(invalid, 'Building Accessories')
    assert choose_type(row, item, facts, types, ai)[0] == 'Building Accessories'
    assert len(ai.calls) == 2
    retry = ai.calls[1][1]
    assert retry['selection_attempt'] == 2
    assert len(retry['candidates']) == len(types)
    assert all('description' not in value for value in retry['candidates'])


def test_retry_never_invents_type_or_repeats_indefinitely():
    row, item, facts = source()
    ai = AI('Canopy', 'Not In This Store')
    with pytest.raises(ListingAIError, match='已扩大官方类目检索并重试'):
        choose_type(row, item, facts, [option('Awnings')], ai)
    assert len(ai.calls) == 2
    assert row['product_type'] == ''


def test_normalize_only_unambiguous_official_name():
    row, item, facts = source()
    assert choose_type(row, item, facts, [option('Awnings & Covers')], AI('  awnings and covers  '))[0] == 'Awnings & Covers'
    with pytest.raises(ListingAIError):
        choose_type(row, item, facts, [option('Awnings & Covers'), option('Awnings and Covers')],
                    AI('awnings and covers', 'awnings and covers'))


def test_outdoor_type_without_word_outdoor_keeps_manual_choice():
    row, item, facts = source()
    row['product_type'] = 'Awnings'
    ai = AI()
    assert choose_type(row, item, facts, [option('Awnings')], ai)[1]['kind'] == 'existing'
    assert not ai.calls


@pytest.mark.parametrize('changed', [dict(source_sku='another'), dict(refs=['detail.NoSuchField']),
                                   dict(confidence=.5), dict(reason='')])
def test_identity_evidence_and_confidence_still_required(changed):
    row, item, facts = source()
    class Invalid(AI):
        def query(self, purpose, payload):
            return super().query(purpose, payload) | changed
    ai = Invalid('Awnings')
    with pytest.raises(ListingAIError):
        choose_type(row, item, facts, [option('Awnings')], ai)
    assert len(ai.calls) == 1


def test_large_taxonomy_routes_to_official_group_before_recovery():
    row, item, facts = source()
    types = [option(f'Unrelated Type {i}', 'Unrelated Group ' + str(i // 20)) for i in range(1800)]
    types += [option('Awnings', 'Door & Window Shelters', 'Home Improvement')]
    class GroupAI(AI):
        def query(self, purpose, payload):
            if purpose == 'product_type_groups':
                self.calls.append((purpose, payload))
                return dict(source_sku=row['source_sku'], group_ids=[next(
                    v['id'] for v in payload['candidates'] if v['group'] == 'Door & Window Shelters')])
            return super().query(purpose, payload)
    ai = GroupAI('', 'Awnings')
    assert choose_type(row, item, facts, types, ai)[0] == 'Awnings'
    assert [purpose for purpose, _ in ai.calls] == ['product_type', 'product_type_groups', 'product_type']
    assert [v['name'] for v in ai.calls[-1][1]['candidates']] == ['Awnings']


def test_empty_official_taxonomy_stops_without_ai():
    row, item, facts = source()
    ai = AI()
    with pytest.raises(ListingAIError, match='未返回可用商品类型'):
        choose_type(row, item, facts, [], ai)
    assert not ai.calls
