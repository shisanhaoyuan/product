"""Server-side sessions and role checks for an explicitly enabled team deployment."""
import hashlib
import re
import secrets
import sqlite3
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

COOKIE = 'catalog_session'
ROLES = {'admin', 'editor', 'viewer'}
SESSION_SECONDS = 12 * 60 * 60
ITERATIONS = 600_000


def password_hash(password):
    if not 12 <= len(password) <= 256:
        raise ValueError('密码须为 12–256 个字符')
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f'pbkdf2_sha256${ITERATIONS}${salt}${digest}'


def password_matches(password, stored):
    algorithm, count, salt, expected = stored.split('$')
    if algorithm != 'pbkdf2_sha256':
        return False
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(count)).hex()
    return secrets.compare_digest(actual, expected)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def public_user(row):
    return {k: row[k] for k in ('id', 'username', 'role', 'active', 'must_change_password')}


class AuthStore:
    def __init__(self, catalog):
        self.catalog = catalog
        self.dummy_hash = password_hash(secrets.token_urlsafe(32))
        with catalog.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users(
                    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL, role TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    must_change_password INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions(
                    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    csrf_token TEXT NOT NULL, expires_at INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS session_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS auth_attempts(
                    key TEXT PRIMARY KEY, started INTEGER NOT NULL, attempts INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS audit_events(
                    id INTEGER PRIMARY KEY, at INTEGER NOT NULL, actor_id INTEGER,
                    action TEXT NOT NULL, target TEXT NOT NULL);
                INSERT OR IGNORE INTO migrations VALUES(2);
            ''')

    def audit(self, db, actor, action, target=''):
        db.execute('INSERT INTO audit_events(at,actor_id,action,target) VALUES(?,?,?,?)',
                   (int(time.time()), actor, action, str(target)))

    def create_user(self, username, password, role='viewer', actor=None, temporary=True):
        username = username.strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,63}', username):
            raise ValueError('账号须为 3–64 位小写英文字母、数字、点、横线或下划线')
        if role not in ROLES:
            raise ValueError('角色无效')
        hashed = password_hash(password)
        try:
            with self.catalog.connect() as db:
                cursor = db.execute('INSERT INTO users(username,password_hash,role,must_change_password) VALUES(?,?,?,?)',
                                    (username, hashed, role, int(temporary)))
                self.audit(db, actor, 'user.create', username)
                return public_user(db.execute('SELECT * FROM users WHERE id=?', (cursor.lastrowid,)).fetchone())
        except sqlite3.IntegrityError as exc:
            raise ValueError('账号已经存在') from exc

    def login(self, username, password, ip):
        username = username.strip().lower()
        now = int(time.time())
        # Bound both one account across IPs and many accounts from one IP.
        keys = [(digest('user:' + username), 10), (digest('ip:' + ip), 60)]
        blocked = False
        with self.catalog.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM auth_attempts WHERE started < ?', (now - 900,))
            for key, limit in keys:
                db.execute('INSERT INTO auth_attempts VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET attempts=attempts+1', (key, now))
                count = db.execute('SELECT attempts FROM auth_attempts WHERE key=?', (key,)).fetchone()[0]
                blocked |= count > limit
            row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if blocked:
            raise HTTPException(429, '登录尝试过多，请在 15 分钟后重试', headers={'Retry-After': '900'})
        valid = password_matches(password, row['password_hash'] if row else self.dummy_hash)
        if not valid or not row or not row['active']:
            raise HTTPException(401, '账号或密码错误')
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.catalog.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Recheck under the write lock so disable/reset cannot race a login.
            current = db.execute('SELECT * FROM users WHERE id=?', (row['id'],)).fetchone()
            if not current['active'] or current['password_hash'] != row['password_hash']:
                raise HTTPException(401, '账号或密码错误')
            db.execute('DELETE FROM sessions WHERE expires_at <= ?', (now,))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (digest(token), row['id'], csrf, now + SESSION_SECONDS))
            db.execute('DELETE FROM auth_attempts WHERE key=?', (keys[0][0],))
            self.audit(db, row['id'], 'login')
        return token, csrf, public_user(current)

    def session(self, token):
        if not token or len(token) > 128:
            return None
        with self.catalog.connect() as db:
            row = db.execute('''SELECT users.*, sessions.csrf_token FROM sessions JOIN users ON users.id=sessions.user_id
                WHERE token_hash=? AND expires_at>? AND active=1''', (digest(token), int(time.time()))).fetchone()
        return dict(row) if row else None

    def update_user(self, user_id, role, active, actor):
        with self.catalog.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if not row:
                raise HTTPException(404, '账号不存在')
            if row['role'] == 'admin' and row['active'] and (role != 'admin' or not active):
                if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] <= 1:
                    raise HTTPException(409, '必须保留至少一位启用的管理员')
            db.execute('UPDATE users SET role=?,active=? WHERE id=?', (role, int(active), user_id))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
            self.audit(db, actor, 'user.update', f'{user_id}:{role}:{active}')

    def change_password(self, user_id, old_password, new_password):
        hashed = password_hash(new_password)
        with self.catalog.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if not password_matches(old_password, row['password_hash']):
                raise HTTPException(400, '当前密码不正确')
            if password_matches(new_password, row['password_hash']):
                raise HTTPException(400, '新密码不能与当前密码相同')
            db.execute('UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?', (hashed, user_id))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
            self.audit(db, user_id, 'password.change')


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class NewUser(LoginInput):
    role: Literal['admin', 'editor', 'viewer'] = 'viewer'


class UpdateUser(BaseModel):
    role: Literal['admin', 'editor', 'viewer']
    active: bool


class PasswordInput(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


def auth_routes(store, secure_cookie):
    router = APIRouter()

    @router.get('/api/auth/me')
    def me(request: Request):
        if not store:
            return {'enabled': False, 'user': None, 'csrf': ''}
        user = store.session(request.cookies.get(COOKIE))
        return {'enabled': True, 'user': public_user(user) if user else None, 'csrf': user['csrf_token'] if user else ''}

    @router.post('/api/auth/login')
    def login(data: LoginInput, request: Request, response: Response):
        if not store:
            raise HTTPException(404)
        token, csrf, user = store.login(data.username, data.password, request.client.host if request.client else 'unknown')
        with store.catalog.connect() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(request.cookies.get(COOKIE, '')),))
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=secure_cookie, samesite='strict', path='/')
        return {'enabled': True, 'user': user, 'csrf': csrf}

    @router.post('/api/auth/logout')
    def logout(request: Request, response: Response):
        with store.catalog.connect() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(request.cookies.get(COOKIE, '')),))
            store.audit(db, request.state.user['id'], 'logout')
        response.delete_cookie(COOKIE, secure=secure_cookie, httponly=True, samesite='strict', path='/')
        return {'ok': True}

    @router.post('/api/auth/password')
    def change_password(data: PasswordInput, request: Request, response: Response):
        store.change_password(request.state.user['id'], data.old_password, data.new_password)
        response.delete_cookie(COOKIE, secure=secure_cookie, httponly=True, samesite='strict', path='/')
        return {'ok': True}

    @router.get('/api/admin/users')
    def users():
        with store.catalog.connect() as db:
            return [public_user(row) for row in db.execute('SELECT * FROM users ORDER BY id')]

    @router.post('/api/admin/users')
    def create(data: NewUser, request: Request):
        try:
            return store.create_user(data.username, data.password, data.role, request.state.user['id'])
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.patch('/api/admin/users/{user_id}')
    def update(user_id: int, data: UpdateUser, request: Request):
        store.update_user(user_id, data.role, data.active, request.state.user['id'])
        return {'ok': True}

    @router.get('/api/admin/audit')
    def audit():
        with store.catalog.connect() as db:
            return [dict(row) for row in db.execute('''SELECT audit_events.*,users.username FROM audit_events
                LEFT JOIN users ON actor_id=users.id ORDER BY audit_events.id DESC LIMIT 200''')]

    return router
