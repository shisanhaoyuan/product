import hashlib
import os
import subprocess
import uuid
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from psycopg import sql
import psycopg

from catalog.app import create_app
from catalog.db import Catalog
from catalog.backup import backup_database
from catalog.migration import migrate_sqlite
from catalog.models import ImportBundle
from catalog.object_storage import FileStorage, StorageError
from test_images import picture
from test_editing import detail, edit
from test_prelisting import product

pytestmark = pytest.mark.storage_integration


@pytest.fixture
def pg(request):
    dsn = request.config.getoption('--postgres-dsn')
    if not dsn:
        pytest.skip('Requires --postgres-dsn with disposable database')
    schema = 'migration_' + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as db:
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    target = Catalog(dsn + ('&' if '?' in dsn else '?') + 'options=' + quote('-csearch_path=' + schema))
    yield target
    with psycopg.connect(dsn, autocommit=True) as db:
        db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_filesystem_integrity_and_paths(tmp_path):
    storage = FileStorage(str(tmp_path/'images'))
    content = picture(); digest = hashlib.sha256(content).hexdigest()
    row = storage.put(digest, content, 'image/png')
    assert storage.put(digest, content, 'image/png') == row
    assert storage.get(row) == content
    with pytest.raises(StorageError): storage.path_for('../escape')
    path = storage.path_for(row['object_key'])
    assert path.stat().st_mode & 0o077 == 0
    path.write_bytes(b'corrupt')
    with pytest.raises(StorageError, match='完整性'): storage.get(row)
    with pytest.raises(StorageError): storage.put(digest, content, 'image/png')
    assert path.read_bytes() == b'corrupt'
    path.unlink(); path.symlink_to(tmp_path/'outside')
    with pytest.raises(StorageError): storage.get(row)


def test_filesystem_route_reload_and_failure(tmp_path, monkeypatch):
    monkeypatch.setenv('CATALOG_IMAGE_STORAGE', 'filesystem')
    monkeypatch.setenv('CATALOG_IMAGE_ROOT', str(tmp_path/'images'))
    app = create_app(tmp_path/'db.sqlite'); client = TestClient(app)
    app.state.catalog.ingest(ImportBundle(namespace='test',label='test',listings=[product()]))
    before = detail(client)
    route = f"/api/products/{before['id']}/images/upload"
    url = client.post(route,content=picture()).json()['url']
    assert edit(client, {'field':'images'}, [url]).status_code == 200
    reopened = TestClient(create_app(app.state.catalog.path))
    assert reopened.get(url).content == client.get(url).content
    assert detail(reopened)['variants'] == before['variants']
    saved = detail(client)
    def fail(*args): raise StorageError('磁盘写入失败')
    monkeypatch.setattr(app.state.image_storage,'put',fail)
    assert client.post(route,content=picture()).status_code == 503
    assert detail(client) == saved


def test_full_migration_images_idempotency_and_backup(pg, tmp_path, monkeypatch):
    monkeypatch.setenv('CATALOG_IMAGE_STORAGE','sqlite')
    source = tmp_path/'source.db'; app = create_app(source); client = TestClient(app)
    app.state.catalog.ingest(ImportBundle(namespace='test',label='test',listings=[product()]))
    p = detail(client)
    url = client.post(f"/api/products/{p['id']}/images/upload?variant_sku=child-0",content=picture()).json()['url']
    assert edit(client, {'field':'images','variant_sku':'child-0'}, [url]).status_code == 200
    assert edit(client, {'field':'price','variant_sku':'child-0'}, '42.99').status_code == 200
    client.post('/api/seller-blacklist',json={'source':'Amazon','seller':'example','reason':'保留原因'})
    from catalog.auth import AuthStore
    AuthStore(app.state.catalog).create_user('migration-admin','long-test-password','admin')
    original = app.state.catalog.get(p['id'])
    storage = FileStorage(str(tmp_path/'images'))
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    report = migrate_sqlite(source,pg,storage)
    assert report['images_verified'] == 1 and report['tables']['users'] == 1
    assert pg.get(p['id']) == original
    assert migrate_sqlite(source,pg,storage)['already_migrated']
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    monkeypatch.setenv('CATALOG_IMAGE_STORAGE','filesystem')
    monkeypatch.setenv('CATALOG_IMAGE_ROOT',str(storage.root))
    moved = TestClient(create_app(pg.path))
    assert moved.get(url).content == client.get(url).content
    assert edit(moved, {'field':'brand'}, 'AfterMigration').status_code == 200
    with pg.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM product_edit_events').fetchone()[0] == 3
    dump = backup_database(pg,tmp_path/'backup.dump')
    assert dump.read_bytes().startswith(b'PGDMP')
    # Parse archive and verify a real restore in a separate temporary database.
    from psycopg.conninfo import conninfo_to_dict
    conn = conninfo_to_dict(pg.path); restore_name = 'restore_' + uuid.uuid4().hex
    conn.pop('options',None)
    with psycopg.connect(**conn, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restore_name)))
    try:
        env = dict(os.environ)
        for key,val in conn.items(): env['PG'+('DATABASE' if key=='dbname' else key.upper())] = val
        env['PGDATABASE'] = restore_name
        subprocess.run(['pg_restore','--exit-on-error','--no-owner','--no-acl','--dbname',restore_name,str(dump)],env=env,check=True,capture_output=True)
        conn['dbname'] = restore_name
        with psycopg.connect(**conn) as restored:
            # schema name is read from the source connection, not URL decoding assumptions.
            with pg.connect() as db: schema = db.execute('SELECT current_schema()').fetchone()[0]
            count = restored.execute(sql.SQL('SELECT COUNT(*) FROM {}.listings').format(sql.Identifier(schema))).fetchone()[0]
            assert count == 1
    finally:
        # Use original target connection for database administration.
        conn = conninfo_to_dict(pg.path); conn.pop('options',None)
        with psycopg.connect(**conn,autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(restore_name)))


def test_migration_corrupt_image_rolls_back(pg,tmp_path):
    source=Catalog(tmp_path/'bad.db')
    source.ingest(ImportBundle(namespace='test',label='test',listings=[product()]))
    with source.connect() as db:
        db.execute('INSERT INTO image_assets(digest,mime,content,created_at,actor_id) VALUES(?,?,?,?,?)',('a'*64,'image/jpeg',b'bad','now',None))
    with pytest.raises(StorageError): migrate_sqlite(source.path,pg,FileStorage(str(tmp_path/'images')))
    assert pg.stats()['listings'] == 0


def test_concurrent_filesystem_deduplication(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    storage=FileStorage(str(tmp_path/'images'))
    content=picture(); digest=hashlib.sha256(content).hexdigest()
    with ThreadPoolExecutor(max_workers=8) as workers:
        rows=list(workers.map(lambda _: storage.put(digest,content,'image/png'), range(24)))
    assert all(row==rows[0] for row in rows)
    assert storage.get(rows[0])==content
    assert len([p for p in storage.root.rglob('*') if p.is_file()])==1


def test_migration_nonempty_target_rejected(pg,tmp_path):
    source=Catalog(tmp_path/'source.db')
    data=ImportBundle(namespace='test',label='test',listings=[product()])
    source.ingest(data); pg.ingest(data)
    before=pg.export()
    with pytest.raises(ValueError,match='已有数据'):
        migrate_sqlite(source.path,pg,FileStorage(str(tmp_path/'images')))
    assert pg.export()==before


def test_migration_missing_referenced_image_rolls_back(pg,tmp_path):
    source=Catalog(tmp_path/'source.db')
    p=product(); p.images=['/api/products/images/assets/'+'a'*64]
    source.ingest(ImportBundle(namespace='test',label='test',listings=[p]))
    with pytest.raises(ValueError,match='缺失的本地图片'):
        migrate_sqlite(source.path,pg,FileStorage(str(tmp_path/'images')))
    assert pg.stats()['listings']==0
