"""Admin-managed, store- and market-scoped Walmart OAuth bindings."""
import hashlib
import json
import time
import uuid
from typing import Literal
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, Field, field_validator

from .credential_vault import encrypt, decrypt, read_key, VaultError
from .database import write_lock, INTEGRITY_ERRORS, is_postgres
from .walmart_client import WalmartError


ACCOUNT_TABLE = '''CREATE TABLE IF NOT EXISTS walmart_accounts(
        store_id INTEGER PRIMARY KEY REFERENCES listing_stores(id),
        marketplace TEXT NOT NULL CHECK(marketplace IN ('US','CA')),
        partner_id TEXT NOT NULL,profile_json TEXT NOT NULL,scopes_json TEXT NOT NULL,
        encrypted_credentials TEXT NOT NULL,client_hint TEXT NOT NULL,
        status TEXT NOT NULL,revision INTEGER NOT NULL,last_verified_at TEXT NOT NULL,
        last_attempt_at TEXT NOT NULL,last_error TEXT NOT NULL,updated_at TEXT NOT NULL)'''


def initialize(db):
    db.execute(ACCOUNT_TABLE)
    if not db.execute('SELECT 1 FROM migrations WHERE version=22').fetchone():
        if is_postgres(db):
            db.execute('ALTER TABLE walmart_accounts DROP CONSTRAINT IF EXISTS walmart_accounts_marketplace_check')
            db.execute("ALTER TABLE walmart_accounts ADD CONSTRAINT walmart_accounts_marketplace_check CHECK(marketplace IN ('US','CA'))")
        else:
            # No tables reference this table; store IDs reference listing_stores.
            # Copy credentials verbatim, retaining US encryption compatibility.
            db.execute(ACCOUNT_TABLE.replace('walmart_accounts(', 'walmart_accounts_v22('))
            db.execute('INSERT INTO walmart_accounts_v22 SELECT * FROM walmart_accounts')
            db.execute('DROP TABLE walmart_accounts')
            db.execute('ALTER TABLE walmart_accounts_v22 RENAME TO walmart_accounts')
        db.execute('INSERT INTO migrations VALUES(22)')
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS walmart_partner_binding
        ON walmart_accounts(marketplace,partner_id) WHERE encrypted_credentials<>''""")
    db.execute('''CREATE TABLE IF NOT EXISTS walmart_binding_previews(
        id TEXT PRIMARY KEY,store_id INTEGER NOT NULL REFERENCES listing_stores(id),
        actor_id INTEGER NOT NULL,expected_revision INTEGER NOT NULL,
        encrypted_credentials TEXT NOT NULL,client_hint TEXT NOT NULL,
        profile_json TEXT NOT NULL,scopes_json TEXT NOT NULL,expires_at BIGINT NOT NULL)''')
    if is_postgres(db):
        db.execute("ALTER TABLE walmart_binding_previews ADD COLUMN IF NOT EXISTS marketplace TEXT NOT NULL DEFAULT 'US' CHECK(marketplace IN ('US','CA'))")
    elif 'marketplace' not in {r['name'] for r in db.execute('PRAGMA table_info(walmart_binding_previews)')}:
        db.execute("ALTER TABLE walmart_binding_previews ADD COLUMN marketplace TEXT NOT NULL DEFAULT 'US' CHECK(marketplace IN ('US','CA'))")
    db.execute('''CREATE TABLE IF NOT EXISTS walmart_account_events(
        id TEXT PRIMARY KEY,store_id INTEGER NOT NULL REFERENCES listing_stores(id),
        actor_id INTEGER NOT NULL,action TEXT NOT NULL,at TEXT NOT NULL,message TEXT NOT NULL)''')
    from .store_items import initialize as initialize_offer_changes
    initialize_offer_changes(db)
    db.execute('INSERT INTO migrations VALUES(15) ON CONFLICT DO NOTHING')


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Credentials(Input):
    store_id: int = Field(gt=0)
    marketplace: Literal['US', 'CA'] = 'US'
    client_id: SecretStr
    client_secret: SecretStr

    @field_validator('client_id', 'client_secret')
    @classmethod
    def valid_secret(cls, value):
        raw = value.get_secret_value()
        if not 1 <= len(raw) <= 1000 or raw != raw.strip() or not raw.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in raw):
            raise ValueError('密钥格式无效')
        return value

    @field_validator('client_id')
    @classmethod
    def valid_id(cls, value):
        if ':' in value.get_secret_value():
            raise ValueError('Client ID 格式无效')
        return value


class Confirm(Input):
    verification_id: uuid.UUID


class Revision(Input):
    revision: int = Field(ge=0)


async def body(request, model):
    # FastAPI's default validation errors may echo secret input: sanitize all errors here.
    try:
        raw = await request.body()
        if len(raw) > 8192:
            raise ValueError()
        return model.model_validate_json(raw)
    except (ValidationError, ValueError):
        raise HTTPException(422, '提交格式无效，请检查店铺、密钥或版本后重试') from None


def account(db, store_id):
    return db.execute('SELECT * FROM walmart_accounts WHERE store_id=?', (store_id,)).fetchone()


def revision(row):
    return row['revision'] if row else 0


def public(row):
    if not row:
        return None
    return {k: row[k] for k in ('store_id', 'marketplace', 'client_hint', 'status', 'revision',
                                'last_verified_at', 'last_attempt_at', 'last_error', 'updated_at')} | {
        'profile': json.loads(row['profile_json']), 'scopes': json.loads(row['scopes_json']),
        'bound': bool(row['encrypted_credentials'])}


def store(db, store_id):
    row = db.execute('SELECT * FROM listing_stores WHERE id=?', (store_id,)).fetchone()
    if not row:
        raise HTTPException(404, '系统店铺不存在')
    if not row['active']:
        raise HTTPException(409, '系统店铺已停用，请先启用')
    return row


def event(db, store_id, actor, action, message=''):
    db.execute('INSERT INTO walmart_account_events VALUES(?,?,?,?,?,?)',
               (str(uuid.uuid4()), store_id, actor, action, now(), message))


def check_revision(row, expected):
    if revision(row) != expected:
        raise HTTPException(409, '绑定状态已被修改，请刷新后重新操作')


def binding_identity(marketplace, profile, credentials):
    if marketplace == 'US':
        return profile['partnerId']
    # This is an INTERNAL credential identity, never a Walmart Partner ID.
    # CA's documented OAuth endpoints do not return the seller name/ID.
    return 'oauth:' + hashlib.sha256(credentials['client_id'].encode()).hexdigest()


def compatible(db, store_id, identity, marketplace):
    existing = account(db, store_id)
    if existing and existing['encrypted_credentials'] and (
            existing['marketplace'] != marketplace or existing['partner_id'] != identity):
        raise HTTPException(409, '站点或账号身份发生变化；请核对密钥，更换站点或加拿大 Client ID 需先解绑')
    duplicate = db.execute("SELECT store_id FROM walmart_accounts WHERE partner_id=? AND marketplace=? AND encrypted_credentials<>'' AND store_id<>?",
                           (identity, marketplace, store_id)).fetchone()
    if duplicate:
        raise HTTPException(409, '该沃尔玛店铺已绑定其他系统店铺，请先解除原绑定，避免串店')


def walmart_routes(catalog):
    router = APIRouter(prefix='/api/walmart')

    def actor(request):
        return getattr(request.state, 'user', {}).get('id', 0)

    def verifier(request):
        return request.app.state.walmart_verifier

    async def verify_credentials(request, credentials, marketplace):
        # Keep US verifier callers and existing integrations compatible.
        options = {} if marketplace == 'US' else {'marketplace': marketplace}
        return await run_in_threadpool(verifier(request).verify, **credentials, **options)

    @router.get('/accounts')
    def accounts():
        try:
            key = read_key()
            vault = {'ready': True, 'key_id': hashlib.sha256(key).hexdigest()[:12], 'message': ''}
        except VaultError as exc:
            vault = {'ready': False, 'key_id': '', 'message': str(exc)}
        with catalog.connect() as db:
            rows = db.execute('SELECT * FROM listing_stores ORDER BY lower(name)').fetchall()
            from .listing_prerequisites import preparation_settings
            result=[]
            for s in rows:
                bound=account(db,s['id'])
                result.append(dict(store_id=s['id'],name=s['name'],active=bool(s['active']),sku_code=s['sku_code'],
                    account=public(bound),default_brand=preparation_settings(db,bound).get('default_brand','')
                    if bound and bound['encrypted_credentials'] else ''))
        return dict(vault=vault, stores=result, execution_enabled=True)

    @router.post('/accounts/preview')
    async def preview(request: Request):
        data = await body(request, Credentials)
        try:
            read_key()
            with catalog.connect() as db:
                store(db, data.store_id)
                expected = revision(account(db, data.store_id))
            credentials = dict(client_id=data.client_id.get_secret_value(), client_secret=data.client_secret.get_secret_value())
            verified = await verify_credentials(request, credentials, data.marketplace)
            identity = binding_identity(data.marketplace, verified['profile'], credentials)
            ciphertext = encrypt(data.store_id, credentials, data.marketplace)
        except (VaultError, WalmartError) as exc:
            raise HTTPException(400, str(exc)) from None
        with catalog.connect() as db:
            write_lock(db)
            store(db, data.store_id)
            check_revision(account(db, data.store_id), expected)
            compatible(db, data.store_id, identity, data.marketplace)
            db.execute('DELETE FROM walmart_binding_previews WHERE expires_at<=?', (int(time.time()),))
            db.execute('DELETE FROM walmart_binding_previews WHERE store_id=? AND actor_id=?', (data.store_id, actor(request)))
            draft = str(uuid.uuid4()); expires = int(time.time()) + 600
            # Only the requesting administrator can confirm this short-lived encrypted draft.
            db.execute('''INSERT INTO walmart_binding_previews
                (id,store_id,actor_id,expected_revision,encrypted_credentials,client_hint,profile_json,scopes_json,expires_at,marketplace)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                       (draft, data.store_id, actor(request), expected, ciphertext,
                        '••••' + (credentials['client_id'][-4:] if len(credentials['client_id']) > 4 else ''), encode(verified['profile']), encode(verified['scopes']), expires, data.marketplace))
            event(db, data.store_id, actor(request), 'preview')
        return dict(verification_id=draft, store_id=data.store_id, marketplace=data.marketplace, expires_at=expires, **verified)

    @router.post('/accounts/{store_id}/bind')
    async def bind(store_id: int, request: Request):
        data = await body(request, Confirm)
        try:
            with catalog.connect() as db:
                write_lock(db)
                from .walmart_publishing import assert_not_sending
                assert_not_sending(db, store_id)
                store(db, store_id)
                draft = db.execute('SELECT * FROM walmart_binding_previews WHERE id=? AND store_id=? AND actor_id=?',
                                   (str(data.verification_id), store_id, actor(request))).fetchone()
                if not draft or draft['expires_at'] <= time.time():
                    raise HTTPException(409, '验证预览已过期或不可用，请重新验证密钥')
                credentials = decrypt(store_id, draft['encrypted_credentials'], draft['marketplace'])
                check_revision(account(db, store_id), draft['expected_revision'])
                profile = json.loads(draft['profile_json'])
                identity = binding_identity(draft['marketplace'], profile, credentials)
                compatible(db, store_id, identity, draft['marketplace'])
                at = now()
                db.execute('''INSERT INTO walmart_accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(store_id) DO UPDATE SET marketplace=excluded.marketplace,partner_id=excluded.partner_id,profile_json=excluded.profile_json,
                    scopes_json=excluded.scopes_json,encrypted_credentials=excluded.encrypted_credentials,
                    client_hint=excluded.client_hint,status=excluded.status,revision=excluded.revision,
                    last_verified_at=excluded.last_verified_at,last_attempt_at=excluded.last_attempt_at,
                    last_error='',updated_at=excluded.updated_at''',
                    (store_id, draft['marketplace'], identity, draft['profile_json'], draft['scopes_json'],
                     draft['encrypted_credentials'], draft['client_hint'], 'verified', draft['expected_revision']+1, at, at, '', at))
                db.execute('DELETE FROM walmart_binding_previews WHERE store_id=?', (store_id,))
                event(db, store_id, actor(request), 'bind')
                return public(account(db, store_id))
        except VaultError as exc:
            raise HTTPException(400, str(exc)) from None
        except INTEGRITY_ERRORS:
            raise HTTPException(409, '店铺绑定冲突，请刷新后核对') from None

    @router.post('/accounts/{store_id}/verify')
    async def verify(store_id: int, request: Request):
        data = await body(request, Revision)
        with catalog.connect() as db:
            store(db, store_id)
            row = account(db, store_id)
            check_revision(row, data.revision)
            if not row or not row['encrypted_credentials']:
                raise HTTPException(409, '店铺尚未绑定')
        error = ''
        try:
            credentials = decrypt(store_id, row['encrypted_credentials'], row['marketplace'])
            verified = await verify_credentials(request, credentials, row['marketplace'])
            if binding_identity(row['marketplace'], verified['profile'], credentials) != row['partner_id']:
                raise WalmartError('沃尔玛返回身份与已绑定店铺不一致，请核对后重新绑定')
        except (VaultError, WalmartError) as exc:
            error = str(exc)
        with catalog.connect() as db:
            write_lock(db)
            from .walmart_publishing import assert_not_sending
            assert_not_sending(db, store_id)
            store(db, store_id)
            current = account(db, store_id)
            check_revision(current, data.revision)
            at = now()
            db.execute('''UPDATE walmart_accounts SET status=?,revision=revision+1,last_attempt_at=?,
                last_verified_at=?,last_error=?,profile_json=?,scopes_json=?,updated_at=? WHERE store_id=?''',
                ('error' if error else 'verified', at, row['last_verified_at'] if error else at, error,
                 row['profile_json'] if error else encode(verified['profile']),
                 row['scopes_json'] if error else encode(verified['scopes']), at, store_id))
            event(db, store_id, actor(request), 'verify_failed' if error else 'verify', error)
            return public(account(db, store_id))

    @router.post('/accounts/{store_id}/unbind')
    async def unbind(store_id: int, request: Request):
        data = await body(request, Revision)
        with catalog.connect() as db:
            write_lock(db)
            from .walmart_publishing import assert_not_sending
            assert_not_sending(db, store_id)
            # Unbinding a disabled store is allowed.
            row = account(db, store_id)
            check_revision(row, data.revision)
            if not row or not row['encrypted_credentials']:
                raise HTTPException(409, '店铺尚未绑定')
            db.execute("""UPDATE walmart_accounts SET encrypted_credentials='',client_hint='',status='unbound',
                revision=revision+1,last_error='',updated_at=? WHERE store_id=?""", (now(), store_id))
            db.execute('DELETE FROM walmart_binding_previews WHERE store_id=?', (store_id,))
            event(db, store_id, actor(request), 'unbind')
            return public(account(db, store_id))

    return router
