"""Permanent SKU reservations, independent of cancellable prelisting batches."""
import json
import re
import secrets
import string
from collections import Counter

from fastapi import HTTPException

from .blacklist import seller_key
from .database import is_postgres

LEGACY_RULE = 'my_store_random6_v1'
RULE = 'my_store_random6_v2'
ALPHABET = string.ascii_uppercase + string.digits


def random_suffix():
    while True:
        value = ''.join(secrets.choice(ALPHABET) for _ in range(6))
        if any(c in string.ascii_uppercase for c in value) and any(c in string.digits for c in value):
            return value


def inferred_code(name):
    match = re.search(r'(?:^|[-\s])([0-9]{1,2})$', name.strip())
    return match[1].zfill(2) if match else None


def fallback_code(name):
    match = re.search(r'(?:^|[-\s])([A-Za-z])[-\s]+([0-9]{1,2})$', name.strip())
    return match[1].upper() + match[2][0] if match else None


def available_code(db, name):
    for code in (inferred_code(name), fallback_code(name)):
        if code and not db.execute('SELECT 1 FROM listing_stores WHERE sku_code=?', (code,)).fetchone():
            return code
    return None


def initialize(db):
    if is_postgres(db):
        db.execute('ALTER TABLE listing_stores ADD COLUMN IF NOT EXISTS sku_code TEXT')
    elif 'sku_code' not in {r['name'] for r in db.execute('PRAGMA table_info(listing_stores)')}:
        db.execute('ALTER TABLE listing_stores ADD COLUMN sku_code TEXT')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS store_sku_code ON listing_stores(sku_code)')
    create_table(db, 'platform_skus', if_missing=True)
    if not db.execute('SELECT 1 FROM migrations WHERE version=11').fetchone():
        if is_postgres(db):
            db.execute('ALTER TABLE platform_skus DROP CONSTRAINT IF EXISTS platform_skus_random_code_check')
            db.execute("""ALTER TABLE platform_skus ADD CONSTRAINT platform_skus_random_code_check
                CHECK(length(random_code)=6 AND random_code ~ '^[A-Z0-9]{6}$')""")
        else:
            create_table(db, 'platform_skus_v11')
            db.execute('INSERT INTO platform_skus_v11 SELECT * FROM platform_skus')
            db.execute('DROP TABLE platform_skus')
            db.execute('ALTER TABLE platform_skus_v11 RENAME TO platform_skus')
        db.execute('INSERT INTO migrations VALUES(11)')
    if not db.execute('SELECT 1 FROM migrations WHERE version=9').fetchone():
        backfill_codes(db)
        db.execute('INSERT INTO migrations VALUES(9)')
    if not db.execute('SELECT 1 FROM migrations WHERE version=10').fetchone():
        backfill_codes(db)
        db.execute('INSERT INTO migrations VALUES(10)')


def create_table(db, table, *, if_missing=False):
    chars_check = "random_code ~ '^[A-Z0-9]{6}$'" if is_postgres(db) else "random_code NOT GLOB '*[^A-Z0-9]*'"
    db.execute(f'''CREATE TABLE {'IF NOT EXISTS ' if if_missing else ''}{table}(
        store_id INTEGER NOT NULL REFERENCES listing_stores(id),
        source_key TEXT NOT NULL, identity_key TEXT NOT NULL,
        random_code TEXT NOT NULL UNIQUE CHECK(length(random_code)=6 AND {chars_check}),
        platform_sku TEXT NOT NULL UNIQUE CHECK(length(platform_sku)=10),
        created_at TEXT NOT NULL,
        PRIMARY KEY(store_id,source_key,identity_key))''')


def backfill_codes(db):
    stores = db.execute('SELECT id,name,sku_code FROM listing_stores').fetchall()
    codes = Counter(inferred_code(s['name']) for s in stores)
    used = {s['sku_code'] for s in stores if s['sku_code']}
    for store in stores:
        code = inferred_code(store['name'])
        if code and codes[code] == 1 and code not in used and store['sku_code'] is None:
            db.execute('UPDATE listing_stores SET sku_code=? WHERE id=?', (code, store['id']))
            used.add(code)
    # Resolve ambiguous numeric suffixes only when the shop-specific alternative is unique.
    pending = db.execute('SELECT id,name FROM listing_stores WHERE sku_code IS NULL').fetchall()
    alternatives = Counter(fallback_code(s['name']) for s in pending)
    for store in pending:
        code = fallback_code(store['name'])
        if code and alternatives[code] == 1 and code not in used:
            db.execute('UPDATE listing_stores SET sku_code=? WHERE id=?', (code, store['id']))
            used.add(code)


def identity(source, row):
    source_key = seller_key(source)
    asin = (row.get('asin') or '').strip().upper()
    value = asin or (row.get('sku') or '').strip()
    if not source_key or not value:
        raise HTTPException(409, '缺少来源或子商品身份，无法分配平台 SKU')
    return source_key, json.dumps(['asin' if asin else 'sku', value], separators=(',', ':'))


def lookup(db, store_id, source, row):
    record = db.execute('''SELECT platform_sku FROM platform_skus
        WHERE store_id=? AND source_key=? AND identity_key=?''', (store_id, *identity(source, row))).fetchone()
    return record['platform_sku'] if record else None


def allocate(db, store, source, row, at):
    existing = lookup(db, store['id'], source, row)
    if existing:
        if not existing.startswith('MY' + store['sku_code']):
            raise HTTPException(409, '平台 SKU 与目标店铺编码不一致，请核查')
        return existing
    key = identity(source, row)
    for _ in range(100):
        random_code = random_suffix()
        sku = 'MY' + store['sku_code'] + random_code
        # ON CONFLICT preserves the PostgreSQL transaction when a random code collides.
        inserted = db.execute('''INSERT INTO platform_skus
            (store_id,source_key,identity_key,random_code,platform_sku,created_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT DO NOTHING''',
            (store['id'], *key, random_code, sku, at)).rowcount
        if inserted:
            return sku
        existing = lookup(db, store['id'], source, row)
        if existing:
            return existing
    raise HTTPException(409, '随机 SKU 分配冲突过多，本次未创建批次，请重试或检查编码容量')
