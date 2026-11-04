"""Optional real PostgreSQL parity run; each test owns a disposable schema."""
import uuid
from urllib.parse import quote
import pytest


def pytest_addoption(parser):
    parser.addoption('--postgres-dsn', default=None, help='Disposable PostgreSQL database for parity tests')


@pytest.fixture(autouse=True)
def postgres_parity(request, monkeypatch, tmp_path):
    dsn = request.config.getoption('--postgres-dsn')
    if not dsn or request.node.get_closest_marker('storage_integration'):
        yield
        return
    if request.node.name in ('test_upload_save_backup_isolated', 'test_local_mode_does_not_add_users'):
        pytest.skip('SQLite-specific assertion; covered by standard suite')
    import psycopg
    from psycopg import sql
    from catalog.db import Catalog
    original = Catalog.__init__
    schemas = {}
    def initialize(self, path):
        if str(path).startswith(('postgresql://', 'postgres://')):
            return original(self, path)
        key = str(path)
        if key not in schemas:
            schema = 'test_' + uuid.uuid4().hex
            with psycopg.connect(dsn, autocommit=True) as db:
                db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            schemas[key] = schema
        url = dsn + ('&' if '?' in dsn else '?') + 'options=' + quote('-csearch_path=' + schemas[key])
        original(self, url)
    monkeypatch.setattr(Catalog, '__init__', initialize)
    monkeypatch.setenv('CATALOG_IMAGE_STORAGE', 'filesystem')
    monkeypatch.setenv('CATALOG_IMAGE_ROOT', str(tmp_path/'images'))
    yield
    with psycopg.connect(dsn, autocommit=True) as db:
        for schema in schemas.values():
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
