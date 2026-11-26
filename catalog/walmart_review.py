"""Cross-store submission progress, using durable attempts as the only source.

GET never contacts Walmart. Manual polling reuses the store/Partner/Feed/SKU
checks and leases of publishing, explicitly disabling inventory writes.
"""
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from .database import read_snapshot
from .walmart_publishing import admin, refresh_attempt
from .walmart_publication import normalize_saved_result

Status = Literal['', 'processing', 'published', 'failed', 'ingested', 'unpublished',
                 'stock_pending', 'unknown', 'rejected']
LABELS = {
    'processing': '平台处理中', 'published': '已发布', 'failed': '审核失败',
    'ingested': '资料已接收，待发布', 'unpublished': '未发布',
    'stock_pending': '库存待核验', 'unknown': '结果待确认', 'rejected': '提交被拒绝',
}


def queue_sql(postgres):
    # Expand only submitted rows, not unsubmitted documents. JSON keys are data,
    # including SKUs with punctuation: never concatenate them into JSON paths.
    if postgres:
        expanded = "CROSS JOIN LATERAL jsonb_array_elements(CAST(a.rows_json AS jsonb)) AS entry(value)"
        field = lambda name: f"entry.value->>'{name}'"
        title = "entry.value->'visible'->>'productName'"
        image = "entry.value->'visible'->>'mainImageUrl'"
        brand = "CAST(b.preview_json AS jsonb)->>'brand'"
        result = "CAST(a.results_json AS jsonb)->(entry.value->>'platform_sku')"
        state = "result_json->>'status'"
        published = "result_json->>'published_status'"
        market = "CASE WHEN CAST(a.payload_json AS jsonb)->'MPItemFeedHeader'->>'mart'='WALMART_CA' THEN 'CA' ELSE 'US' END"
    else:
        expanded = 'JOIN json_each(a.rows_json) AS entry'
        field = lambda name: f"json_extract(entry.value,'$.{name}')"
        title = "json_extract(entry.value,'$.visible.productName')"
        image = "json_extract(entry.value,'$.visible.mainImageUrl')"
        brand = "json_extract(b.preview_json,'$.brand')"
        result = f"(SELECT value FROM json_each(a.results_json) WHERE key={field('platform_sku')})"
        state = "json_extract(result_json,'$.status')"
        published = "json_extract(result_json,'$.published_status')"
        market = "CASE WHEN json_extract(a.payload_json,'$.MPItemFeedHeader.mart')='WALMART_CA' THEN 'CA' ELSE 'US' END"
    return f'''WITH expanded AS (
        SELECT a.id AS attempt_id,a.batch_id,a.store_id,a.partner_id,a.state AS attempt_state,
               a.feed_id,a.message AS attempt_message,a.created_at,a.updated_at,s.name AS store_name,
               {market} AS marketplace,
               {field('platform_sku')} AS platform_sku,{field('source_sku')} AS source_sku,
               {field('listing_id')} AS listing_id,COALESCE({title},'') AS title,
               COALESCE({image},'') AS image_url,
               COALESCE({brand},'') AS brand,{result} AS result_json,
               t.state AS polling_state,t.next_at,t.last_error,
               CASE WHEN w.partner_id=a.partner_id AND w.marketplace=({market}) AND w.status='verified'
                         AND w.encrypted_credentials<>'' AND s.active=1 THEN 1 ELSE 0 END AS binding_matches
        FROM walmart_listing_attempts a JOIN prelisting_batches b ON b.id=a.batch_id
        JOIN listing_stores s ON s.id=a.store_id {expanded}
        LEFT JOIN listing_result_tasks t ON t.attempt_id=a.id
        LEFT JOIN walmart_accounts w ON w.store_id=a.store_id
    ), ranked AS (
        SELECT *,ROW_NUMBER() OVER (PARTITION BY store_id,partner_id,platform_sku
                                   ORDER BY created_at DESC,attempt_id DESC) AS position,
               CASE WHEN {state}='ingested' AND upper({published})='SYSTEM_PROBLEM' THEN 'unpublished'
                    WHEN {state} IN ('processing','published','failed','ingested','unpublished',
                                     'stock_pending','unknown','rejected') THEN {state}
                    WHEN attempt_state='rejected' THEN 'rejected' ELSE 'unknown' END AS status
        FROM expanded
    ) '''


def public_row(record):
    item = dict(record)
    result = item.pop('result_json') or {}
    if isinstance(result, str):
        result = json.loads(result)
    result = normalize_saved_result(result)
    item['message'] = result.get('message') or item.pop('attempt_message') or '尚未获得完整平台结果'
    item.pop('attempt_message', None)
    item['errors'] = result.get('errors') or []
    item['stock_verified'] = result.get('stock_verified') is True
    item['stock_message'] = result.get('stock_message', '')
    item['published_status'] = result.get('published_status', '')
    item['status_label'] = LABELS[item['status']]
    item['is_latest'] = item.pop('position') == 1
    item['can_refresh'] = bool(item['feed_id'] and item['binding_matches'])
    item['binding_matches'] = bool(item['binding_matches'])
    return item


def review_routes(catalog):
    router = APIRouter(prefix='/api/walmart-review')

    @router.get('')
    def queue(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
              store_id: int = Query(0, ge=0), status: Status = '',
              q: str = Query('', max_length=200), history: bool = False):
        with catalog.connect() as db:
            read_snapshot(db)
            prefix = queue_sql(catalog.postgres)
            conditions = [] if history else ['position=1']
            args = []
            if store_id:
                conditions.append('store_id=?'); args.append(store_id)
            if q.strip():
                # Literal search: '_' and '%' in SKUs must not become wildcards.
                needle = q.strip().lower().replace('!', '!!').replace('%', '!%').replace('_', '!_')
                columns = ('platform_sku','source_sku','title','feed_id','batch_id','store_name','brand')
                conditions.append('(' + ' OR '.join(f"lower({c}) LIKE ? ESCAPE '!'" for c in columns) + ')')
                args.extend(['%' + needle + '%'] * len(columns))
            relation = ' FROM ranked WHERE ' + (' AND '.join(conditions) if conditions else '1=1')
            counts = {key: 0 for key in LABELS}
            for row in db.execute(prefix + 'SELECT status,count(*) AS n' + relation + ' GROUP BY status', args):
                counts[row['status']] = row['n']
            total = counts[status] if status else sum(counts.values())
            if status:
                relation += ' AND status=?'; args.append(status)
            rows = db.execute(prefix + 'SELECT *' + relation +
                ' ORDER BY created_at DESC,attempt_id DESC,platform_sku LIMIT ? OFFSET ?',
                (*args,page_size,(page-1)*page_size)).fetchall()
            stores = [dict(row) for row in db.execute('''SELECT DISTINCT s.id,s.name FROM listing_stores s
                JOIN walmart_listing_attempts a ON a.store_id=s.id ORDER BY s.name''')]
            return dict(items=[public_row(row) for row in rows], total=total, page=page,
                        page_size=page_size, counts=counts, stores=stores)

    @router.post('/attempts/{aid}/refresh')
    def refresh(aid: str, request: Request):
        admin(request)
        with catalog.connect() as db:
            row = db.execute('SELECT batch_id FROM walmart_listing_attempts WHERE id=?', (aid,)).fetchone()
            if not row:
                raise HTTPException(404, '提交记录不存在')
        return refresh_attempt(catalog, row['batch_id'], aid, request, sync_inventory=False)

    return router
