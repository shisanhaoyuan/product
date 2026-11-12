"""Folder summaries over immutable per-file import receipts (no data migration)."""
import json

COUNTERS = ('created', 'updated', 'unchanged', 'older_skipped', 'snapshots_added')


def context_data(context, actor_id):
    if context is None:
        return None
    return context.model_dump(mode='json') | {'actor': str(actor_id) if actor_id is not None else 'local'}


def context_sql(postgres, key):
    if postgres:
        return f"(result_json::jsonb -> '_context' ->> '{key}')"
    return f"json_extract(result_json, '$._context.{key}')"


def previous_result(db, postgres, context, digest):
    # The write lock makes a retry after a lost HTTP response idempotent.
    where = ' AND '.join(f'{context_sql(postgres, key)}=?' for key in ('batch_id', 'file_id', 'actor'))
    row = db.execute(f'SELECT digest,result_json FROM imports WHERE {where} ORDER BY id DESC LIMIT 1',
                     tuple(context[key] for key in ('batch_id', 'file_id', 'actor'))).fetchone()
    if row is None:
        return None
    stored = json.loads(row['result_json'])
    if row['digest'] != digest or stored['_context'] != context:
        raise ValueError('导入文件批次标识已使用，但文件内容或路径不同，请重新选择文件')
    return {key: stored.get(key, 0) for key in COUNTERS}


def grouped_imports(db, postgres):
    batch = context_sql(postgres, 'batch_id')
    actor = context_sql(postgres, 'actor')
    # Old clients never recorded a folder identity. Label/date grouping is explicitly
    # marked as a legacy summary, not presented as an exact upload session.
    legacy = ("json_build_array(namespace,label,substr(at,1,10))::text" if postgres
              else "json_array(namespace,label,substr(at,1,10))")
    key = f"CASE WHEN {batch} IS NOT NULL THEN 'batch:' || {actor} || ':' || {batch} ELSE 'legacy:' || {legacy} END"
    rows = db.execute(f"""WITH keyed AS (SELECT *, {key} AS group_key FROM imports),
        recent AS (SELECT group_key, MAX(id) AS newest FROM keyed GROUP BY group_key
                   ORDER BY newest DESC LIMIT 100)
        SELECT keyed.* FROM keyed JOIN recent ON keyed.group_key=recent.group_key
        ORDER BY keyed.id DESC""")
    groups = {}
    for row in rows:
        result = json.loads(row['result_json'])
        context = result.get('_context')
        key = row['group_key']
        if key not in groups:
            groups[key] = dict(id=row['id'], at=row['at'], started_at=row['at'],
                label=context['name'] if context else row['label'], namespace=row['namespace'],
                kind=context['kind'] if context else 'legacy', file_count=0,
                result={name: 0 for name in COUNTERS}, files=[])
        group = groups[key]
        group['started_at'] = min(group['started_at'], row['at'])
        group['file_count'] += 1
        counters = {name: result.get(name, 0) for name in COUNTERS}
        for name in COUNTERS:
            group['result'][name] += counters[name]
        group['files'].append(dict(id=row['id'], at=row['at'], label=row['label'],
            namespace=row['namespace'], path=context['path'] if context else '', result=counters))
        if group['namespace'] != row['namespace']:
            group['namespace'] = '多来源'
    return list(groups.values())
