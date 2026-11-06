"""Offline, all-or-nothing SQLite → PostgreSQL migration with independent image byte verification."""
import hashlib
import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .database import write_lock
from .object_storage import save_metadata
from .projection import rebuild

TABLES = ('products','listings','variants','snapshots','imports','users','sessions',
          'auth_attempts','audit_events','seller_blacklist','seller_blacklist_events',
          'product_edits','product_edit_events','listing_stores','platform_skus','prelisting_batches',
          'prelisting_items','prelisting_claims','prelisting_events','image_objects','source_captures')


def migrate_sqlite(source, target, storage):
    if not target.postgres or storage is None:
        raise ValueError('迁移目标必须为 PostgreSQL，并配置独立图片存储')
    source = Path(source).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='jiwu-migration-') as folder:
        snapshot = Path(folder) / 'source.db'
        # The source is read-only; SQLite's backup API takes a consistent snapshot.
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as old, sqlite3.connect(snapshot) as copy:
            old.backup(copy)
        with sqlite3.connect(snapshot) as old, target.connect() as db:
            old.row_factory = sqlite3.Row
            if old.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or old.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('SQLite 完整性检查未通过')
            tables = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {'products','listings','variants','snapshots','imports'}
            if not required.issubset(tables):
                raise ValueError('不是完整的商品库数据库备份')
            source_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            write_lock(db, 'migration')
            write_lock(db)
            write_lock(db, 'auth')
            previous = db.execute('SELECT report_json FROM migration_runs WHERE source_digest=?', (source_hash,)).fetchone()
            if previous:
                return json.loads(previous[0]) | {'already_migrated': True}
            for table in TABLES:
                if table == 'listing_stores':
                    if db.execute("SELECT 1 FROM listing_stores WHERE origin='manual'").fetchone():
                        raise ValueError('目标有自定义店铺，拒绝覆盖')
                elif db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                    raise ValueError(f'目标表 {table} 已有数据，拒绝覆盖；请使用新的空数据库')
            db.execute('DELETE FROM listing_stores')
            counts = {}
            # Preserve IDs and original text exactly, including sessions and audit history.
            for table in TABLES:
                if table not in tables:
                    counts[table] = 0
                    continue
                columns = [r['name'] for r in old.execute(f'PRAGMA table_info({table})')]
                target_columns = {r[0] for r in db.execute('''SELECT column_name FROM information_schema.columns
                    WHERE table_schema=current_schema() AND table_name=?''', (table,))}
                if not set(columns).issubset(target_columns):
                    raise ValueError(f'{table} 包含未知列，迁移已停止')
                if not all(c.replace('_','').isalnum() for c in columns):
                    raise ValueError('源数据库列名异常')
                rows = [tuple(r) for r in old.execute(f'SELECT * FROM {table}')]
                sql_columns = ','.join('"' + c + '"' for c in columns)
                db.executemany(f'INSERT INTO {table}({sql_columns}) VALUES({",".join("?" for _ in columns)})', rows)
                actual = [tuple(r[c] for c in columns) for r in db.execute(f'SELECT {sql_columns} FROM {table}')]
                if sorted(rows, key=repr) != sorted(actual, key=repr):
                    raise ValueError(f'{table} 逐行核验失败，已回滚')
                counts[table] = len(rows)
                if 'id' in columns:
                    sequence = db.execute('SELECT pg_get_serial_sequence(?,?)', (table,'id')).fetchone()[0]
                    if sequence:
                        db.execute(f"SELECT setval(?::regclass,COALESCE(MAX(id),1),COUNT(*)>0) FROM {table}", (sequence,))
            if 'sku_code' not in {r['name'] for r in old.execute('PRAGMA table_info(listing_stores)')}:
                from .platform_sku import backfill_codes
                backfill_codes(db)
            images = 0
            for row in db.execute('SELECT * FROM image_objects').fetchall():
                storage.get(row)  # Existing object references must be readable too.
                images += 1
            if 'image_assets' in tables:
                for row in old.execute('SELECT * FROM image_assets'):
                    metadata = storage.put(row['digest'], row['content'], row['mime'])
                    save_metadata(db, metadata, row['actor_id'], row['created_at'])
                    images += 1
            rebuild(db)
            if db.execute('SELECT COUNT(*) FROM current_listings').fetchone()[0] != counts['listings']:
                raise ValueError('商品读模型数量核验失败')
            # Validate every local image reference, including historical snapshots/drafts.
            known = {r[0] for r in db.execute('SELECT digest FROM image_objects')}
            import re
            for table in ('listings','snapshots','product_edits','prelisting_items','prelisting_batches'):
                for row in db.execute(f'SELECT * FROM {table}'):
                    for value in row.values():
                        if isinstance(value,str):
                            refs = re.findall(r'/api/products/images/assets/([a-f0-9]{64})', value)
                            if not set(refs).issubset(known):
                                raise ValueError('存在缺失的本地图片，迁移已回滚')
            report = dict(source_digest=source_hash, tables=counts, images_verified=images,
                          destination='postgresql+'+storage.backend, already_migrated=False)
            db.execute('INSERT INTO migration_runs VALUES(?,?,?)',
                       (source_hash,datetime.now(timezone.utc).isoformat(),json.dumps(report,ensure_ascii=False)))
            return report
