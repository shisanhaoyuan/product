import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from catalog.ai_dispatch import AIKeyPool, key_pool, _pools
from catalog.listing_ai_client import ListingAIClient, ListingAIError, ListingAISettings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv('CATALOG_LISTING_AI_ENV_FILE', str(tmp_path / 'not-present'))
    monkeypatch.setenv('CATALOG_LISTING_AI_ENABLED', '1')
    monkeypatch.setenv('CATALOG_LISTING_AI_KEY', 'primary-test-key')
    monkeypatch.setenv('CATALOG_LISTING_AI_KEYS', json.dumps([f'additional-test-{i}' for i in range(4)]))
    monkeypatch.setenv('CATALOG_LISTING_AI_BASE_URL', 'https://ai.example.test/v1')
    monkeypatch.setenv('CATALOG_LISTING_AI_STREAM', '0')
    _pools.clear()
    yield ListingAISettings()
    _pools.clear()


def completion():
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]})


def test_five_keys_allow_ten_requests_shared_across_clients(settings):
    barrier = threading.Barrier(10)
    keys = []
    lock = threading.Lock()
    def remote(request):
        with lock:
            keys.append(request.headers['Authorization'])
        barrier.wait(timeout=3)
        return completion()
    transport = httpx.MockTransport(remote)
    with ThreadPoolExecutor(10) as executor:
        responses = list(executor.map(lambda _: ListingAIClient(settings, transport).query('test', {}), range(10)))
    assert responses == [{'ok': True}] * 10
    assert len(set(keys)) == 5
    assert all(keys.count(key) == 2 for key in set(keys))
    assert key_pool(settings.base_url, settings.keys, settings.key_parallel).stats()['active'] == 0
    assert not any(key in json.dumps(settings.public()) for key in settings.keys)


def test_one_key_is_not_used_by_two_requests_at_once():
    pool = AIKeyPool(['private-test'], parallel=1)
    entered, release = threading.Event(), threading.Event()
    active, peak = 0, 0
    lock = threading.Lock()
    def call():
        nonlocal active, peak
        with pool.acquire(timeout=2):
            with lock:
                active += 1
                peak = max(peak, active)
            entered.set()
            release.wait(2)
            with lock:
                active -= 1
    with ThreadPoolExecutor(3) as executor:
        futures = [executor.submit(call) for _ in range(3)]
        try:
            assert entered.wait(1)
        finally:
            release.set()
        for future in futures:
            future.result(2)
    assert peak == 1 and pool.stats()['active'] == 0


def test_rate_limited_key_cools_down_and_other_keys_continue(settings):
    calls = []
    def remote(request):
        key = request.headers['Authorization'].removeprefix('Bearer ')
        calls.append(key)
        return httpx.Response(429, text=key, headers={'Retry-After': '60'}) if key == settings.key else completion()
    client = ListingAIClient(settings, httpx.MockTransport(remote))
    assert client.query('test', {}) == {'ok': True}
    for _ in range(8):
        assert client.query('test', {}) == {'ok': True}
    assert calls.count(settings.key) == 1
    assert len(set(calls)) == 5
    assert key_pool(settings.base_url, settings.keys, settings.key_parallel).stats()['cooling'] == 1


def test_all_rejected_keys_stop_after_one_attempt_each_and_redact_secrets(settings):
    calls = []
    def remote(request):
        calls.append(request.headers['Authorization'])
        return httpx.Response(401, text=request.headers['Authorization'])
    client = ListingAIClient(settings, httpx.MockTransport(remote))
    with pytest.raises(ListingAIError) as error:
        client.query('test', {})
    assert len(calls) == 5
    assert not any(key in str(error.value) for key in settings.keys)
    with pytest.raises(ListingAIError):
        client.query('test', {})
    assert len(calls) == 5


def test_unknown_transport_outcome_is_not_replayed_and_key_slot_is_released(settings):
    calls = []
    def remote(request):
        calls.append(1)
        raise httpx.ReadTimeout('secret upstream detail', request=request)
    with pytest.raises(ListingAIError) as error:
        ListingAIClient(settings, httpx.MockTransport(remote)).query('test', {})
    assert calls == [1] and 'secret upstream detail' not in str(error.value)
    assert key_pool(settings.base_url, settings.keys, settings.key_parallel).stats()['active'] == 0


def test_settings_accept_existing_key_and_deduplicate_additional_keys(settings, monkeypatch):
    monkeypatch.setenv('CATALOG_LISTING_AI_KEYS', json.dumps([settings.key, 'extra', 'extra']))
    assert ListingAISettings().keys == [settings.key, 'extra']
    monkeypatch.setenv('CATALOG_LISTING_AI_KEYS', '{"bad":"private-test-secret"}')
    with pytest.raises(ListingAIError) as error:
        ListingAISettings()
    assert 'private-test-secret' not in str(error.value)


def test_waiters_receive_key_before_hot_caller_can_reacquire():
    pool = AIKeyPool(['test-key'])
    order = []
    def call(name):
        with pool.acquire(timeout=2):
            order.append(name)
    with ThreadPoolExecutor(3) as executor:
        with pool.acquire():
            first = executor.submit(call, 'waiting-operator')
            deadline = time.monotonic() + 1
            while pool.stats()['waiting'] != 1 and time.monotonic() < deadline:
                time.sleep(.005)
            assert pool.stats()['waiting'] == 1
            second = executor.submit(call, 'next-operator')
            deadline = time.monotonic() + 1
            while pool.stats()['waiting'] != 2 and time.monotonic() < deadline:
                time.sleep(.005)
            assert pool.stats()['waiting'] == 2
        # This running batch immediately asks for its next attribute request.
        call('hot-caller')
        first.result(2)
        second.result(2)
    assert order == ['waiting-operator', 'next-operator', 'hot-caller']
    assert pool.stats()['waiting'] == 0


def test_timed_out_waiter_does_not_block_the_next_request():
    from catalog.ai_dispatch import AIChannelsBusy
    pool = AIKeyPool(['test-key'])
    def blocked():
        with pytest.raises(AIChannelsBusy):
            with pool.acquire(timeout=.01):
                pytest.fail('must time out')
    with ThreadPoolExecutor(1) as executor:
        with pool.acquire():
            executor.submit(blocked).result(1)
        with pool.acquire(timeout=.1):
            assert pool.stats()['waiting'] == 0
