import time

import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.auth import COOKIE, digest

ORIGIN = 'https://catalog.example.test'
PASSWORD = 'a-test-password-12345'


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setenv('CATALOG_AUTH_ENABLED', '1')
    monkeypatch.setenv('CATALOG_PUBLIC_ORIGIN', ORIGIN)
    app = create_app(tmp_path / 'team.db')
    for username, role in [('admin', 'admin'), ('editor', 'editor'), ('viewer', 'viewer')]:
        app.state.auth.create_user(username, PASSWORD, role, temporary=False)
    return app


def login(app, name='admin'):
    client = TestClient(app, base_url=ORIGIN)
    response = client.post('/api/auth/login', json={'username': name, 'password': PASSWORD}, headers={'Origin': ORIGIN})
    assert response.status_code == 200
    client.headers.update({'Origin': ORIGIN, 'X-CSRF-Token': response.json()['csrf']})
    return client, response


def test_private_data_requires_login(team):
    client = TestClient(team, base_url=ORIGIN)
    for path in ['/api/products', '/api/products/any', '/api/products/any/snapshots/1', '/api/export.csv', '/api/export.json', '/api/imports', '/api/stats', '/api/import-schema', '/docs', '/openapi.json', '/api/admin/users']:
        assert client.get(path).status_code == 401, path
    assert client.get('/api/auth/me').json() == {'enabled': True, 'user': None, 'csrf': ''}
    assert client.get('/api/health').status_code == 200


def test_secure_session_and_logout(team):
    client, response = login(team)
    cookie = response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=strict' in cookie
    assert client.get('/api/stats').status_code == 200
    assert client.get('/api/auth/me').headers['cache-control'] == 'no-store'
    token = client.cookies.get(COOKIE)
    with team.state.catalog.connect() as db:
        row = db.execute('SELECT * FROM sessions').fetchone()
        assert row['token_hash'] == digest(token) and row['token_hash'] != token
        user = db.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        assert user['password_hash'] != PASSWORD and user['password_hash'].startswith('pbkdf2_sha256$')
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get('/api/products').status_code == 401
    replay = TestClient(team, base_url=ORIGIN)
    replay.cookies.set(COOKIE, token)
    assert replay.get('/api/products').status_code == 401


@pytest.mark.parametrize('role,allowed', [('viewer', False), ('editor', True), ('admin', True)])
def test_import_and_admin_roles(team, role, allowed):
    client, _ = login(team, role)
    response = client.post('/api/imports', json={'schema_version': 1, 'namespace': 'test', 'label': 'test', 'listings': []})
    assert response.status_code == (200 if allowed else 403)
    for method, path, data in [('get', '/api/admin/users', None), ('get', '/api/admin/audit', None), ('post', '/api/admin/users', {'username': 'extra', 'password': PASSWORD, 'role': 'viewer'}), ('patch', '/api/admin/users/2', {'role': 'viewer', 'active': True})]:
        result = getattr(client, method)(path, **({'json': data} if data is not None else {}))
        assert result.status_code == (200 if role == 'admin' else 403)
    with team.state.catalog.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE action='catalog.import'").fetchone()[0] == int(allowed)


def test_origin_csrf_and_token_cannot_bypass_roles(team, monkeypatch):
    client, _ = login(team)
    body = {'username': 'extra', 'password': PASSWORD, 'role': 'viewer'}
    assert client.post('/api/admin/users', json=body, headers={'Origin': 'https://evil.test'}).status_code == 403
    assert client.post('/api/admin/users', json=body, headers={'X-CSRF-Token': ''}).status_code == 403
    assert client.post('/api/admin/users', json=body, headers={'Origin': ''}).status_code == 403
    outsider = TestClient(team, base_url=ORIGIN)
    assert outsider.post('/api/auth/login', json={'username': 'admin', 'password': PASSWORD}).status_code == 403
    assert client.get('/api/stats', headers={'Host': 'evil.test'}).status_code == 400
    monkeypatch.setenv('CATALOG_IMPORT_TOKEN', 'valid-token')
    assert outsider.post('/api/imports', json={}, headers={'X-Import-Token': 'valid-token'}).status_code == 401
    viewer, _ = login(team, 'viewer')
    assert viewer.post('/api/imports', json={}, headers={'X-Import-Token': 'valid-token'}).status_code == 403


def test_disable_and_role_change_revoke_sessions(team):
    admin, _ = login(team)
    editor, _ = login(team, 'editor')
    assert admin.patch('/api/admin/users/2', json={'role': 'viewer', 'active': True}).status_code == 200
    assert editor.get('/api/products').status_code == 401
    viewer, _ = login(team, 'viewer')
    assert admin.patch('/api/admin/users/3', json={'role': 'viewer', 'active': False}).status_code == 200
    assert viewer.get('/api/products').status_code == 401
    assert viewer.post('/api/auth/login', json={'username': 'viewer', 'password': PASSWORD}).status_code == 401


def test_last_admin_protected(team):
    admin, _ = login(team)
    for update in [{'role': 'viewer', 'active': True}, {'role': 'admin', 'active': False}]:
        assert admin.patch('/api/admin/users/1', json=update).status_code == 409
    assert admin.get('/api/admin/users').status_code == 200


def test_temporary_password_and_change_revokes_all_sessions(team):
    admin, _ = login(team)
    assert admin.post('/api/admin/users', json={'username': 'new-user', 'password': PASSWORD, 'role': 'editor'}).status_code == 200
    member, _ = login(team, 'new-user')
    other_session, _ = login(team, 'new-user')
    assert member.get('/api/auth/me').json()['user']['must_change_password']
    assert member.get('/api/products').status_code == 403
    assert member.post('/api/auth/password', json={'old_password': 'incorrect', 'new_password': 'a-new-password-12345'}).status_code == 400
    assert member.post('/api/auth/password', json={'old_password': PASSWORD, 'new_password': PASSWORD}).status_code == 400
    assert member.post('/api/auth/password', json={'old_password': PASSWORD, 'new_password': 'a-new-password-12345'}).status_code == 200
    assert other_session.get('/api/products').status_code == 401
    result = member.post('/api/auth/login', json={'username': 'new-user', 'password': 'a-new-password-12345'})
    assert result.status_code == 200 and not result.json()['user']['must_change_password']
    assert member.get('/api/products').status_code == 200


def test_expiry(team):
    client, _ = login(team)
    with team.state.catalog.connect() as db:
        db.execute('UPDATE sessions SET expires_at=?', (int(time.time()) - 1,))
    assert client.get('/api/stats').status_code == 401


def test_login_rate_limit(team):
    client = TestClient(team, base_url=ORIGIN, headers={'Origin': ORIGIN})
    for _ in range(10):
        assert client.post('/api/auth/login', json={'username': 'missing', 'password': 'incorrect'}).status_code == 401
    assert client.post('/api/auth/login', json={'username': 'missing', 'password': 'incorrect'}).status_code == 429


def test_invalid_auth_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv('CATALOG_AUTH_ENABLED', '1')
    for origin in ['', 'http://public.example.com', 'https://public.example.com/path']:
        monkeypatch.setenv('CATALOG_PUBLIC_ORIGIN', origin)
        with pytest.raises(RuntimeError): create_app(tmp_path / 'bad.db')


def test_local_mode_does_not_add_users(tmp_path, monkeypatch):
    monkeypatch.delenv('CATALOG_AUTH_ENABLED', raising=False)
    app = create_app(tmp_path / 'local.db')
    client = TestClient(app)
    assert client.get('/api/auth/me').json()['enabled'] is False
    assert client.get('/api/products').status_code == 200
    assert client.get('/api/admin/users').status_code == 404
    with app.state.catalog.connect() as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone()
