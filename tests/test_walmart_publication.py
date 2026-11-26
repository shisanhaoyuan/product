import pytest

from catalog.walmart_publication import publication_result


REASON = ('This item has been unpublished due to item content quality. '
          'To republish this item, please make sure you have the appropriate Product Type selected.')


@pytest.mark.parametrize('reasons', [
    {'reason': [REASON]}, {'reason': REASON}, REASON,
    [{'code': 'CONTENT_QUALITY', 'description': REASON}],
    {'reason': {'code': 'CONTENT_QUALITY', 'message': REASON}},
])
def test_system_problem_preserves_content_quality_reason(reasons):
    result = publication_result({'publishedStatus': 'SYSTEM_PROBLEM', 'unpublishedReasons': reasons})
    assert result['status'] == 'unpublished'
    assert result['published_status'] == 'SYSTEM_PROBLEM'
    assert result['errors'][0]['original'] == REASON
    assert '内容质量' in result['errors'][0]['message']


@pytest.mark.parametrize('status,expected', [
    ('PUBLISHED', 'published'), ('UNPUBLISHED', 'unpublished'),
    ('SYSTEM_PROBLEM', 'unpublished'), ('IN_PROGRESS', 'ingested'),
    ('READY_TO_PUBLISH', 'ingested'), ('unexpected', 'unknown'), (None, 'unknown'),
])
def test_publication_status_is_not_inferred_from_feed_success(status, expected):
    result = publication_result({'publishedStatus': status})
    assert result['status'] == expected
    if status == 'SYSTEM_PROBLEM':
        assert '未提供具体原因' in result['message']


def test_reason_code_is_preserved():
    result = publication_result({'publishedStatus': 'UNPUBLISHED', 'unpublishedReasons': {
        'reason': {'code': 'CONTENT_QUALITY', 'description': REASON}}})
    assert result['errors'][0]['code'] == 'CONTENT_QUALITY'
