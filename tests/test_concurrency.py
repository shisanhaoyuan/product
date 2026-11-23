"""Deterministic saturation tests; no external platform or AI calls."""
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.concurrency import AdmissionMiddleware, BoundedExecutor, RequestGate, WorkQueueFull, background_call
from catalog.database import DatabaseBusy
from test_auth import team, login, ORIGIN
from test_walmart_publishing import publishing
from test_giga_prelisting import warehouse
from test_giga import settings
from test_walmart_accounts import vault


def test_executor_bounds_pending_work_and_releases_cancelled_and_failed_slots():
    release = threading.Event()
    entered = threading.Event()
    def hold():
        entered.set()
        assert release.wait(3)
    with BoundedExecutor(max_workers=1, max_pending=1) as pool:
        first = pool.submit(hold)
        try:
            assert entered.wait(1)
            pending = pool.submit(lambda: None)
            with pytest.raises(WorkQueueFull):
                pool.submit(lambda: None)
            assert pending.cancel()
            replacement = pool.submit(lambda: 42)
        finally:
            release.set()
        first.result(1)
        assert replacement.result(1) == 42
        def fail():
            raise ValueError('test')
        with pytest.raises(ValueError):
            pool.submit(fail).result(1)
        assert pool.submit(lambda: 7).result(1) == 7


def test_heavy_lane_saturation_preserves_reads_health_and_never_replays_writes():
    async def scenario():
        app = FastAPI()
        gate = RequestGate(active=4, waiting=0, work=1, images=1, timeout=.05)
        app.add_middleware(AdmissionMiddleware, gate=gate)
        entered, release = asyncio.Event(), asyncio.Event()
        writes = []
        @app.post('/api/work')
        async def work():
            writes.append(1)
            entered.set()
            await release.wait()
            return {'ok': True}
        @app.get('/api/products')
        @app.get('/api/health')
        async def read():
            return {'ok': True}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            first = asyncio.create_task(client.post('/api/work'))
            await entered.wait()
            try:
                overloaded = await asyncio.gather(*(client.post('/api/work') for _ in range(30)))
                assert all(r.status_code == 503 and r.headers['retry-after'] == '3' for r in overloaded)
                assert (await client.get('/api/products')).status_code == 200
                assert (await client.get('/api/health')).status_code == 200
                assert writes == [1]
            finally:
                release.set()
            assert (await first).status_code == 200
        assert gate.stats()['rejected'] == 30
        assert gate.active.total() == 0 and gate.waiting == 0
    asyncio.run(scenario())


def test_wait_queue_times_out_and_cancellation_releases_every_slot():
    async def scenario():
        gate = RequestGate(active=1, waiting=1, work=1, timeout=.02)
        entered = asyncio.Event()
        async def hold():
            async with gate.enter('work'):
                entered.set()
                await asyncio.Event().wait()
        active = asyncio.create_task(hold())
        await entered.wait()
        from catalog.concurrency import AdmissionBusy
        try:
            with pytest.raises(AdmissionBusy):
                async with gate.enter('work'):
                    pytest.fail('must not enter')
            waiting = asyncio.create_task(hold())
            await asyncio.sleep(0)
            assert gate.waiting == 1
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            assert gate.waiting == 0
        finally:
            active.cancel()
            with pytest.raises(asyncio.CancelledError):
                await active
        assert gate.active.total() == 0
        async with gate.enter('read'):
            assert gate.active.total() == 1
    asyncio.run(scenario())


async def during_slow_call(app, path, method, entered, release, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
        task = asyncio.create_task(client.request(method, path, **kwargs))
        try:
            # A blocked event loop cannot observe entry before the fake's timeout.
            before = time.monotonic()
            while not entered.is_set() and time.monotonic() - before < 1:
                await asyncio.sleep(.005)
            assert entered.is_set() and time.monotonic() - before < 1
            assert (await asyncio.wait_for(client.get('/api/health'), .5)).status_code == 200
        finally:
            release.set()
            await task


def test_session_database_lookup_does_not_block_event_loop(team, monkeypatch):
    client, _ = login(team)
    original = team.state.auth.session
    entered, release = threading.Event(), threading.Event()
    def slow(token):
        entered.set()
        release.wait(2)
        return original(token)
    monkeypatch.setattr(team.state.auth, 'session', slow)
    asyncio.run(during_slow_call(team, '/api/stats', 'GET', entered, release,
                                headers={'Cookie': client.headers.get('Cookie', '') or
                                         '; '.join(f'{k}={v}' for k, v in client.cookies.items())}))


def test_json_import_runs_in_a_worker_and_does_not_block_health(tmp_path, monkeypatch):
    app = create_app(tmp_path / 'import.db')
    entered, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        entered.set()
        release.wait(2)
        return {'created': 0}
    monkeypatch.setattr(app.state.catalog, 'ingest', slow)
    asyncio.run(during_slow_call(app, '/api/imports', 'POST', entered, release,
                                json={'schema_version': 1, 'namespace': 'test', 'label': 'test', 'listings': []}))


def test_database_overload_is_sanitized_and_runtime_is_admin_only(team, monkeypatch):
    admin, _ = login(team)
    viewer, _ = login(team, 'viewer')
    assert viewer.get('/api/admin/runtime').status_code == 403
    result = admin.get('/api/admin/runtime')
    assert result.status_code == 200
    assert set(result.json()) == {'requests', 'generation', 'images', 'database', 'ai'}
    def busy(*args, **kwargs):
        raise DatabaseBusy('private-database-password')
    monkeypatch.setattr(team.state.catalog, 'stats', busy)
    response = admin.get('/api/stats')
    assert response.status_code == 503 and 'private-database-password' not in response.text
    monkeypatch.setattr(team.state.auth, 'session', busy)
    assert admin.get('/api/products').status_code == 503


def test_generation_full_executor_keeps_durable_queue_and_dispatch_recovers(publishing, monkeypatch):
    from catalog import listing_generation_jobs as jobs
    from test_listing_generation import AI
    c, catalog, *_, batch, base, remote = publishing
    class Full:
        def submit(self, *args):
            raise WorkQueueFull()
    monkeypatch.setattr(jobs, 'POOL', Full())
    job = jobs.start_job(catalog, c.app.state.walmart_publisher, batch['id'], ai=AI())
    assert job['state'] == 'queued'
    with catalog.connect() as db:
        db.execute('UPDATE listing_generation_jobs SET updated_at=0 WHERE id=?', (job['id'],))
    assert c.get(base + '/generation').json()['job']['state'] == 'queued'
    with BoundedExecutor(max_workers=1, max_pending=0) as executor:
        monkeypatch.setattr(jobs, 'POOL', executor)
        monkeypatch.setattr(jobs, 'ListingAISettings', lambda: type('Settings', (), {'configured': True, 'public': lambda self: {}})())
        monkeypatch.setattr(jobs, 'ListingAIClient', AI)
        jobs.dispatch_queued(catalog, c.app.state.walmart_publisher)
    assert c.get(base + '/generation').json()['job']['state'] == 'complete'
    assert not remote.posts and not remote.stock


def test_generation_dispatch_coalesces_concurrent_requests(tmp_path, monkeypatch):
    from catalog import listing_generation_jobs as jobs
    from types import SimpleNamespace
    entered, release = threading.Event(), threading.Event()
    calls = []
    def run(*args):
        calls.append(1)
        entered.set()
        release.wait(2)
    monkeypatch.setattr(jobs, 'run_job', run)
    catalog = SimpleNamespace(path=str(tmp_path / 'db'))
    with BoundedExecutor(max_workers=2, max_pending=0) as executor:
        monkeypatch.setattr(jobs, 'POOL', executor)
        with ThreadPoolExecutor(8) as callers:
            try:
                assert all(callers.map(lambda _: jobs.schedule_job(catalog, None, 'same-job', None), range(30)))
                assert entered.wait(1) and calls == [1]
            finally:
                release.set()


@pytest.mark.parametrize('limit', ['CATALOG_GENERATION_QUEUE_LIMIT', 'CATALOG_GENERATION_USER_QUEUE_LIMIT'])
def test_generation_durable_queue_cap_preserves_same_job_id(publishing, monkeypatch, limit):
    from catalog import listing_generation_jobs as jobs
    from test_listing_generation import AI
    c, catalog, *_, batch, base, remote = publishing
    job = jobs.start_job(catalog, c.app.state.walmart_publisher, batch['id'], ai=AI(), schedule=False)
    monkeypatch.setenv(limit, '1')
    assert jobs.start_job(catalog, c.app.state.walmart_publisher, batch['id'], ai=AI(), schedule=False)['id'] == job['id']
    with catalog.connect() as db:
        # Retain an older active row and make a different latest job terminal.
        db.execute('''INSERT INTO listing_generation_jobs
            SELECT ?,batch_id,'failed',revision,store_id,partner_id,binding_revision,input_hash,
            actor_id,report_json,message,started_at+1,updated_at FROM listing_generation_jobs WHERE id=?''',
                   ('terminal', job['id']))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        jobs.start_job(catalog, c.app.state.walmart_publisher, batch['id'], ai=AI(), schedule=False)
    assert error.value.status_code == 429


def test_same_operator_third_batch_stays_queued(publishing):
    from catalog import listing_generation_jobs as jobs
    from test_listing_generation import AI
    c, catalog, *_, batch, base, remote = publishing
    first = jobs.start_job(catalog, c.app.state.walmart_publisher, batch['id'], ai=AI(), schedule=False)
    with catalog.connect() as db:
        db.execute("UPDATE listing_generation_jobs SET state='running' WHERE id=?", (first['id'],))
        db.execute('''INSERT INTO listing_generation_jobs
            SELECT ?,batch_id,'running',revision,store_id,partner_id,binding_revision,input_hash,
            actor_id,report_json,message,started_at+1,updated_at FROM listing_generation_jobs WHERE id=?''',
                   ('second-running-batch', first['id']))
        db.execute('''INSERT INTO listing_generation_jobs
            SELECT ?,batch_id,'queued',revision,store_id,partner_id,binding_revision,input_hash,
            actor_id,report_json,message,started_at+1,updated_at FROM listing_generation_jobs WHERE id=?''',
                   ('next-batch', first['id']))
    jobs.run_job(catalog, c.app.state.walmart_publisher, 'next-batch', AI())
    with catalog.connect() as db:
        assert db.execute('SELECT state FROM listing_generation_jobs WHERE id=?', ('next-batch',)).fetchone()[0] == 'queued'
    assert not remote.posts and not remote.stock


def test_new_batch_cannot_jump_older_queued_batch_from_same_operator(publishing):
    from catalog import listing_generation_jobs as jobs
    from test_listing_generation import AI
    c, catalog, *_, batch, base, remote = publishing
    first = jobs.start_job(catalog, c.app.state.walmart_publisher, batch['id'], ai=AI(), schedule=False)
    with catalog.connect() as db:
        db.execute('''INSERT INTO listing_generation_jobs
            SELECT ?,batch_id,'queued',revision,store_id,partner_id,binding_revision,input_hash,
            actor_id,report_json,message,started_at+1,updated_at FROM listing_generation_jobs WHERE id=?''',
                   ('newer-batch', first['id']))
    jobs.run_job(catalog, c.app.state.walmart_publisher, 'newer-batch', AI())
    with catalog.connect() as db:
        assert db.execute('SELECT state FROM listing_generation_jobs WHERE id=?', ('newer-batch',)).fetchone()[0] == 'queued'
    jobs.run_job(catalog, c.app.state.walmart_publisher, first['id'], AI())
    with catalog.connect() as db:
        assert db.execute('SELECT state FROM listing_generation_jobs WHERE id=?', (first['id'],)).fetchone()[0] == 'complete'
    assert not remote.posts and not remote.stock


def test_five_operators_run_two_batches_each_despite_one_users_older_backlog(publishing, monkeypatch):
    from catalog import listing_generation_jobs as jobs
    from test_listing_generation import AI
    c, catalog, *_, batch, base, remote = publishing
    seed = jobs.start_job(catalog, c.app.state.walmart_publisher, batch['id'], ai=AI(), schedule=False)
    with catalog.connect() as db:
        # One operator submitted twenty jobs before the other four arrived.
        for i, actor in enumerate([1] * 20 + [2, 2, 3, 3, 4, 4, 5, 5]):
            db.execute('''INSERT INTO listing_generation_jobs
                SELECT ?,batch_id,'queued',revision,store_id,partner_id,binding_revision,input_hash,
                ?,report_json,message,started_at+?,updated_at FROM listing_generation_jobs WHERE id=?''',
                       (f'operator-job-{i}', actor, i, seed['id']))
        db.execute('DELETE FROM listing_generation_jobs WHERE id=?', (seed['id'],))
    barrier = threading.Barrier(11)
    first_round = threading.Event()
    release = threading.Event()
    actors = []
    lock = threading.Lock()
    def run(catalog, publisher, jid, ai):
        from catalog.database import write_lock
        with catalog.connect() as db:
            write_lock(db)
            actor = db.execute('SELECT actor_id FROM listing_generation_jobs WHERE id=?', (jid,)).fetchone()[0]
            db.execute("UPDATE listing_generation_jobs SET state='running' WHERE id=?", (jid,))
        with lock:
            actors.append(actor)
            if len(actors) == 5:
                first_round.set()
        barrier.wait(5)
        assert release.wait(5)
    monkeypatch.setattr(jobs, 'run_job', run)
    monkeypatch.setattr(jobs, 'ListingAISettings', lambda: type('Settings', (), {'configured': True})())
    monkeypatch.setattr(jobs, 'ListingAIClient', AI)
    with BoundedExecutor(max_workers=10, max_pending=0) as executor:
        monkeypatch.setattr(jobs, 'POOL', executor)
        try:
            jobs.dispatch_queued(catalog, c.app.state.walmart_publisher)
            assert first_round.wait(5)
            jobs.dispatch_queued(catalog, c.app.state.walmart_publisher)
            barrier.wait(5)
            assert sorted(actors) == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
        finally:
            release.set()
    # The first operator's next eighteen jobs cannot displace running operators.
    scheduled = []
    monkeypatch.setattr(jobs, 'schedule_job', lambda *args: scheduled.append(args[2]) or True)
    jobs.dispatch_queued(catalog, c.app.state.walmart_publisher)
    assert not scheduled
    with catalog.connect() as db:
        db.execute("UPDATE listing_generation_jobs SET state='complete' WHERE id='operator-job-0'")
    jobs.dispatch_queued(catalog, c.app.state.walmart_publisher)
    assert scheduled == ['operator-job-2']
    assert not remote.posts and not remote.stock


def test_worker_cancellation_waits_for_database_user_before_pool_cleanup():
    async def scenario():
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def work():
            entered.set()
            release.wait(2)
            finished.set()
        task = asyncio.create_task(background_call(work))
        while not entered.is_set():
            await asyncio.sleep(.005)
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
    asyncio.run(scenario())
