import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from catalog.database import DatabaseBusy, read_snapshot
from test_storage import pg

pytestmark = pytest.mark.storage_integration


def test_pool_reuses_connections_rolls_back_and_resets_local_state(pg, monkeypatch):
    pg.close()
    monkeypatch.setenv('CATALOG_DB_POOL_SIZE', '1')
    with pg.connect() as db:
        before = db.execute('SELECT pg_backend_pid()').fetchone()[0]
    with pytest.raises(ValueError):
        with pg.connect() as db:
            db.execute("SET LOCAL application_name='rollback-test'")
            raise ValueError('rollback')
    with pg.connect() as db:
        read_snapshot(db)
        assert db.execute('SELECT pg_backend_pid()').fetchone()[0] == before
        assert db.execute("SELECT current_setting('application_name')").fetchone()[0] == 'jiwu'
    assert pg.pool().get_stats()['connections_num'] == 1


def test_pool_bounds_connection_count_and_wait_queue_then_recovers(pg, monkeypatch):
    pg.close()
    monkeypatch.setenv('CATALOG_DB_POOL_SIZE', '1')
    monkeypatch.setenv('CATALOG_DB_POOL_WAITING', '1')
    monkeypatch.setenv('CATALOG_DB_POOL_TIMEOUT', '1')
    def contend():
        with pytest.raises(DatabaseBusy):
            with pg.connect():
                pytest.fail('pool must time out')
    with pg.connect() as held:
        with ThreadPoolExecutor(1) as executor:
            waiting = executor.submit(contend)
            deadline = time.monotonic() + 1
            while pg.pool().get_stats()['requests_waiting'] == 0 and time.monotonic() < deadline:
                time.sleep(.005)
            assert pg.pool().get_stats()['requests_waiting'] == 1
            before = time.monotonic()
            with pytest.raises(DatabaseBusy):
                with pg.connect():
                    pytest.fail('waiting queue is full')
            assert time.monotonic() - before < .5
            waiting.result(2)
    with pg.connect() as db:
        assert db.execute('SELECT 1').fetchone()[0] == 1
    assert pg.pool().get_stats()['connections_num'] == 1


def test_parallel_database_work_stays_within_pool_size(pg, monkeypatch):
    pg.close()
    monkeypatch.setenv('CATALOG_DB_POOL_SIZE', '2')
    def read(_):
        with pg.connect() as db:
            return db.execute('SELECT pg_backend_pid(),pg_sleep(0.01)').fetchone()[0]
    with ThreadPoolExecutor(8) as executor:
        pids = list(executor.map(read, range(40)))
    assert len(set(pids)) <= 2
    assert pg.pool().get_stats()['pool_available'] == 2
