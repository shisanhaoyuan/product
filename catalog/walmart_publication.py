"""Publication state is independent of feed ingestion and inventory success."""
from .walmart_listing_schema import upstream_reason


def publication_errors(value):
    """Accept string, singleton object and list reason envelopes without losing codes."""
    if isinstance(value, list):
        return [error for item in value for error in publication_errors(item)]
    if isinstance(value, str):
        return upstream_reason([{'description': value}]) if value.strip() else []
    if isinstance(value, dict):
        for key in ('reason', 'reasons', 'error', 'errors'):
            if key in value:
                return publication_errors(value[key])
        description = value.get('description') or value.get('message') or ''
        code = value.get('code') or value.get('reasonCode') or ''
        if description or code:
            return upstream_reason([{'code': code, 'description': description}])
    return []


def publication_result(item):
    raw = item.get('publishedStatus')
    published = raw.strip().upper() if isinstance(raw, str) else ''
    errors = publication_errors(item.get('unpublishedReasons'))
    if published == 'PUBLISHED':
        status, message, errors = 'published', '沃尔玛已确认商品发布成功', []
    elif published in ('UNPUBLISHED', 'SYSTEM_PROBLEM'):
        status = 'unpublished'
        message = ('沃尔玛返回发布异常（SYSTEM_PROBLEM），不是待审核；请核对平台退回原因'
                   if published == 'SYSTEM_PROBLEM' else '沃尔玛确认商品未发布，请处理平台退回原因')
        if not errors:
            message += '。本次 API 未提供具体原因，请在卖家中心查看'
    elif published in ('IN_PROGRESS', 'READY_TO_PUBLISH', 'STAGE') and not errors:
        status, message = 'ingested', '商品资料处理成功，等待平台发布'
    else:
        status, message = 'unknown', '资料已接收，但发布状态尚未确认'
        if published:
            message += '（平台原始状态：' + published + '）'
    return dict(status=status, message=message, errors=errors, published_status=published)


def normalize_saved_result(result):
    # Earlier versions mislabeled SYSTEM_PROBLEM as waiting and dropped its reasons.
    # Correct the projection immediately; the next poll obtains current reasons.
    if result.get('status') == 'ingested' and str(result.get('published_status', '')).upper() == 'SYSTEM_PROBLEM':
        corrected = publication_result({'publishedStatus': 'SYSTEM_PROBLEM'})
        if result.get('errors'):
            corrected['errors'] = result['errors']
        return result | corrected
    return result
