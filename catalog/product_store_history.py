"""Display local plans and actual marketplace results without conflating the two."""
import json


def histories(db, listing_ids):
    result={lid:[] for lid in listing_ids}
    if not result:return result
    marks=','.join('?' for _ in result)
    pairs=db.execute(f'''SELECT target.id AS target_id,related.id AS related_id
        FROM listings target JOIN listings related ON related.product_id=target.product_id
        WHERE target.id IN ({marks})''',list(result)).fetchall()
    targets={}
    for pair in pairs:targets.setdefault(pair['related_id'],[]).append(pair['target_id'])
    if not targets:return result
    marks=','.join('?' for _ in targets)
    records=db.execute(f'''SELECT i.listing_id,b.id,b.status,b.preview_json,b.updated_at,t.batch_id AS trashed
        FROM prelisting_items i JOIN prelisting_batches b ON b.id=i.batch_id
        LEFT JOIN prelisting_batch_trash t ON t.batch_id=b.id
        WHERE i.listing_id IN ({marks})
        ORDER BY b.created_at DESC,b.id DESC''',list(targets)).fetchall()
    bids=list(dict.fromkeys(r['id'] for r in records));attempts={}
    if bids:
        marks=','.join('?' for _ in bids)
        for attempt in db.execute(f'SELECT * FROM walmart_listing_attempts WHERE batch_id IN ({marks}) ORDER BY created_at,id',bids):
            outcomes=json.loads(attempt['results_json'])
            for row in json.loads(attempt['rows_json']):
                status=outcomes.get(row['platform_sku'],{}).get('status',attempt['state'] if attempt['state'] in ('sending','processing','failed') else 'unknown')
                attempts[(attempt['batch_id'],row['platform_sku'])]=(status,attempt['id'])
    for record in records:
        preview=json.loads(record['preview_json'])
        item=next((i for i in preview['items'] if i['listing_id']==record['listing_id']),None)
        if not item:continue
        counts={};aid=''
        for variant in item['variants']:
            status,attempt_id=attempts.get((record['id'],variant.get('platform_sku')),
                ('cancelled' if record['status']=='cancelled' else 'draft',''))
            counts[status]=counts.get(status,0)+1
            aid=attempt_id or aid
        if not aid and (record['status']=='cancelled' or record['trashed']):continue
        entry=dict(store_id=preview['store']['id'],store_name=preview['store']['name'],brand=preview['brand'],
            batch_id=record['id'],attempt_id=aid,counts=counts,updated_at=record['updated_at'])
        for target in targets[record['listing_id']]:
            if entry not in result[target]:result[target].append(entry)
    return result
