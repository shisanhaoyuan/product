"""Transactional PostgreSQL read model, derived from immutable sources + edits."""
import hashlib
import json

from .blacklist import seller_key
from .editing import local_product, local_search


def category(data):
    key = 'GIGA category' if data.get('source') == 'GigaB2B' else 'dc_productType'
    value = data.get('fields', {}).get(key)
    value = value.strip('\t\n\r ') if isinstance(value, str) else ''
    return '' if value.lower() in ('', '请选择', '请选择类目', 'please select', 'select', '--') else value


def refresh(db, lid):
    row = db.execute('SELECT * FROM listings WHERE id=?', (lid,)).fetchone()
    edit = db.execute('SELECT * FROM product_edits WHERE listing_id=?', (lid,)).fetchone() or {}
    raw = local_product(row['data_json'], edit.get('changes_json'), edit.get('revision'),
                        edit.get('updated_at'), edit.get('source_digest'), row['digest'])
    data = json.loads(raw)
    values = {k: row[k] for k in ('id','product_id','namespace','external_id','sku','store',
                                   'template','uploaded_at','observed_at','evidence_kind')}
    values.update(brand=data['brand'], saved=int(data['save_succeeded']), complete=int(data['fill_complete']),
                  verified=int(data['readback_verified']),search_text=local_search(raw),data_json=raw,
                  digest=hashlib.sha256(raw.encode()).hexdigest(),source_digest=row['digest'],
                  edit_version=edit.get('revision',0),category=category(data))
    columns = ','.join(values)
    updates = ','.join(f'{k}=excluded.{k}' for k in values if k != 'id')
    db.execute(f'INSERT INTO current_listings({columns}) VALUES({",".join("?" for _ in values)}) '
               f'ON CONFLICT(id) DO UPDATE SET {updates}', list(values.values()))
    db.execute('DELETE FROM current_listing_sellers WHERE listing_id=?', (lid,))
    sellers = {seller_key(data.get('seller')), *[seller_key(v.get('seller')) for v in data['variants']]}
    db.executemany('INSERT INTO current_listing_sellers VALUES(?,?,?)',
                   [(lid,seller_key(data['source']),name) for name in sellers if name])


def refresh_blacklist(db, entry):
    db.execute('UPDATE seller_blacklist SET search_text=? WHERE id=?',
               (seller_key(' '.join(entry[k] for k in ('source','seller','reason'))),entry['id']))


def rebuild(db):
    for row in db.execute('SELECT id FROM listings').fetchall():
        refresh(db, row['id'])
    for row in db.execute('SELECT * FROM seller_blacklist').fetchall():
        refresh_blacklist(db, row)
