"""Read-only product filters shared by pagination, export and batch selection."""
import json
import re


def normalize_multi(value):
    if not value:
        return ''
    try:
        groups = json.loads(value)
    except (ValueError, TypeError):
        raise ValueError('多选筛选格式不正确') from None
    allowed = {'store', 'brand', 'date', 'category', 'listed_store', 'price_change', 'seller_status', 'inventory_status', 'shipping_address', 'return_address'}
    if not isinstance(groups, dict) or not set(groups).issubset(allowed):
        raise ValueError('包含不支持的多选筛选项')
    result = {}
    for key, values in groups.items():
        if not isinstance(values, list) or len(values) > 50 or any(not isinstance(v, str) or not v or len(v)>300 for v in values):
            raise ValueError('每个筛选项最多选择50个有效值')
        for v in values:
            if key in {'shipping_address', 'return_address'} and not re.fullmatch('[a-f0-9]{64}', v):
                raise ValueError('地址筛选值无效')
            if key=='listed_store' and not re.fullmatch('[0-9]+', v):
                raise ValueError('上架店铺筛选值无效')
            enums = {'price_change': {'up','down','unchanged','unknown'}, 'seller_status': {'blocked','unblocked'}, 'inventory_status': {'out_of_stock'}}
            if key in enums and v not in enums[key]:
                raise ValueError('筛选值无效')
        if values:
            result[key] = list(dict.fromkeys(values))
    return json.dumps(result, ensure_ascii=False, separators=(',', ':')) if result else ''


def json_text(column, *path, postgres=False):
    if postgres:
        return f"({column}::jsonb #>> '{{{','.join(path)}}}')"
    return f"CAST(json_extract({column},'$.{'.'.join(path)}') AS TEXT)"


def store_clause(postgres):
    """Only the current SKU claim's API-confirmed publication counts as listed.

    A feed ID/SUCCESS ingestion or a draft alone is not publication. Keep row,
    outcome, claim and partner identities aligned, including multi-product feeds.
    """
    if postgres:
        rows = 'jsonb_array_elements(a.rows_json::jsonb) AS submitted(value)'
        outcomes = 'jsonb_each(a.results_json::jsonb) AS outcome(key,value)'
    else:
        rows = 'json_each(a.rows_json) AS submitted'
        outcomes = 'json_each(a.results_json) AS outcome'
    def field(column, name):
        return json_text(column, name, postgres=postgres)
    return f'''EXISTS (
        SELECT 1 FROM listings related
        JOIN prelisting_items i ON i.listing_id=related.id
        JOIN walmart_listing_attempts a ON a.batch_id=i.batch_id
        JOIN walmart_listing_claims claim ON claim.attempt_id=a.id
            AND claim.batch_id=a.batch_id AND claim.store_id=a.store_id
        JOIN walmart_accounts account ON account.store_id=a.store_id
            AND account.partner_id=a.partner_id
        CROSS JOIN {rows}
        CROSS JOIN {outcomes}
        WHERE related.product_id=listings.product_id AND CAST(a.store_id AS TEXT)=?
            AND a.feed_id<>''
            AND {field('submitted.value', 'listing_id')}=related.id
            AND {field('submitted.value', 'platform_sku')}=claim.platform_sku
            AND outcome.key=claim.platform_sku
            AND {field('outcome.value', 'status')}='published'
            AND {field('outcome.value', 'published_status')}='PUBLISHED')'''


def price_clause(direction, postgres):
    """Latest source-price comparison per SKU, never ERP/multiplied selling prices.

    Giga snapshots include imports/syncs; patrol before/after includes unchanged runs.
    Amazon uses captured source offers, partitioned by both child SKU and ASIN.
    """
    def field(col, *path):
        return json_text(col, *path, postgres=postgres)
    def number(col):
        if postgres:
            return f"CASE WHEN {col} ~ '^[0-9]+([.][0-9]+)?$' THEN CAST({col} AS NUMERIC) END"
        return f"""CASE WHEN {col}!='' AND {col} NOT GLOB '*[^0-9.]*'
            AND {col} GLOB '[0-9]*' AND {col} NOT LIKE '%.%.%'
            AND substr({col},-1)!='.' THEN CAST({col} AS NUMERIC) END"""
    comparator = {'up': "SUM(CASE WHEN delta>0 THEN 1 ELSE 0 END)>0",
                  'down': "SUM(CASE WHEN delta<0 THEN 1 ELSE 0 END)>0",
                  'unchanged': 'COUNT(delta)=COUNT(*) AND MAX(delta)=0 AND MIN(delta)=0',
                  'unknown': 'COUNT(delta)<COUNT(*)'}[direction]
    query = f'''WITH source_observations AS (
        SELECT s.listing_id,'' AS target,s.observed_at AS at,CAST(s.id AS TEXT) AS tie,
            COALESCE({field('s.data_json','source_facts','price')},{field('s.data_json','price')}) AS price,
            {field('s.data_json','source_facts','currency')} AS currency
        FROM snapshots s JOIN listings l ON l.id=s.listing_id JOIN products p ON p.id=l.product_id
        WHERE p.source IN ('GigaB2B','GigaB2B_CA','Saleyee')
        UNION ALL
        SELECT c.listing_id,c.variant_sku || ':' || c.asin,c.captured_at,c.id,
            COALESCE({field('c.data_json','basis_price')},{field('c.data_json','price')}),
            {field('c.data_json','currency')}
        FROM source_captures c JOIN listings l ON l.id=c.listing_id JOIN products p ON p.id=l.product_id
        WHERE p.source='Amazon' AND (c.variant_sku='' AND c.asin=p.asin
            AND NOT EXISTS (SELECT 1 FROM variants v WHERE v.listing_id=c.listing_id) OR EXISTS (
            SELECT 1 FROM variants v WHERE v.listing_id=c.listing_id AND v.sku=c.variant_sku AND v.asin=c.asin))
    ), paired AS (
        SELECT *,LAG(price) OVER (PARTITION BY listing_id,target ORDER BY at,tie) AS old_price,
            LAG(currency) OVER (PARTITION BY listing_id,target ORDER BY at,tie) AS old_currency
        FROM source_observations
    ), comparisons AS (
        SELECT listing_id,target,at,tie,price,currency,old_price,old_currency,0 AS priority FROM paired
        UNION ALL
        SELECT listing_id,'',checked_at,run_id,
            {field('after_json','price')},{field('after_json','currency')},
            {field('before_json','price')},{field('before_json','currency')},1
        FROM giga_patrol_items WHERE status='ok'
    ), ranked AS (
        SELECT *,ROW_NUMBER() OVER (PARTITION BY listing_id,target ORDER BY at DESC,priority DESC,tie DESC) AS n
        FROM comparisons
    ), deltas AS (
        SELECT listing_id,target,CASE WHEN currency=old_currency AND currency!=''
            THEN ({number('price')})-({number('old_price')}) END AS delta
        FROM ranked WHERE n=1
    ), expected AS (
        SELECT l.id AS listing_id,CASE WHEN p.source IN ('GigaB2B','GigaB2B_CA','Saleyee') THEN '' ELSE ':' || p.asin END AS target
        FROM listings l JOIN products p ON p.id=l.product_id
        WHERE p.source IN ('GigaB2B','GigaB2B_CA','Saleyee') OR NOT EXISTS (SELECT 1 FROM variants v WHERE v.listing_id=l.id)
        UNION ALL
        SELECT v.listing_id,v.sku || ':' || v.asin FROM variants v
        JOIN listings l ON l.id=v.listing_id JOIN products p ON p.id=l.product_id WHERE p.source='Amazon'
    ), complete_deltas AS (
        SELECT e.listing_id,d.delta FROM expected e LEFT JOIN deltas d
            ON d.listing_id=e.listing_id AND d.target=e.target
    ) '''
    if direction == 'unknown':
        query += "SELECT id FROM listings WHERE id NOT IN (SELECT listing_id FROM complete_deltas GROUP BY listing_id HAVING COUNT(delta)=COUNT(*))"
    else:
        query += f'SELECT listing_id FROM complete_deltas GROUP BY listing_id HAVING {comparator}'
    return f'listings.id IN ({query})'
