"""Local seller decisions, kept separate from imported product evidence."""
import json
import unicodedata
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .database import INTEGRITY_ERRORS, write_lock, insert_id, is_postgres


def seller_key(value):
    return ' '.join(unicodedata.normalize('NFKC', value or '').split()).casefold()


BLOCKED_SQL = """EXISTS (SELECT 1 FROM seller_blacklist b
    WHERE b.active=1 AND b.source_key=seller_key(json_extract(listings.data_json,'$.source'))
    AND (b.seller_key=seller_key(json_extract(listings.data_json,'$.seller'))
         OR EXISTS (SELECT 1 FROM json_each(listings.data_json,'$.variants') v
                    WHERE b.seller_key=seller_key(json_extract(v.value,'$.seller')))))"""


class ReasonInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator('reason')
    @classmethod
    def nonempty(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('拉黑原因不能为空')
        return value


class NewSeller(ReasonInput):
    source: str = Field(min_length=1, max_length=100)
    seller: str = Field(min_length=1, max_length=300)

    @field_validator('source', 'seller')
    @classmethod
    def identity(cls, value):
        value = ' '.join(unicodedata.normalize('NFKC', value).split())
        if not value:
            raise ValueError('来源平台和卖家名称不能为空')
        return value


class SellerUpdate(ReasonInput):
    active: bool


def initialize(db):
    if is_postgres(db):
        return
    db.executescript('''
        CREATE TABLE IF NOT EXISTS seller_blacklist(
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, seller TEXT NOT NULL,
            source_key TEXT NOT NULL, seller_key TEXT NOT NULL,
            reason TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(source_key,seller_key));
        CREATE TABLE IF NOT EXISTS seller_blacklist_events(
            id INTEGER PRIMARY KEY, blacklist_id INTEGER NOT NULL REFERENCES seller_blacklist(id),
            at TEXT NOT NULL, actor_id INTEGER, action TEXT NOT NULL, data_json TEXT NOT NULL);
        INSERT OR IGNORE INTO migrations VALUES(3);
    ''')


def public_entry(row):
    return {k: v for k, v in dict(row).items() if k != 'search_text'}


def active_entries(db):
    return {(r['source_key'], r['seller_key']): public_entry(r)
            for r in db.execute('SELECT * FROM seller_blacklist WHERE active=1')}


def matches(data, entries):
    found = {}
    source = seller_key(data.get('source'))
    for name in [data.get('seller'), *[v.get('seller') for v in data.get('variants', [])]]:
        entry = entries.get((source, seller_key(name)))
        if entry:
            found[entry['id']] = entry
    return list(found.values())


def blacklist_routes(catalog):
    router = APIRouter(prefix='/api/seller-blacklist')

    def record(db, entry, request, action):
        if catalog.postgres:
            from .projection import refresh_blacklist
            refresh_blacklist(db, entry)
        user = getattr(request.state, 'user', None)
        db.execute('''INSERT INTO seller_blacklist_events(blacklist_id,at,actor_id,action,data_json)
            VALUES(?,?,?,?,?)''', (entry['id'], entry['updated_at'], user['id'] if user else None,
                                  action, json.dumps(entry, ensure_ascii=False)))

    @router.get('')
    def find(q: str = '', status: Literal['active', 'inactive', 'all'] = 'active',
             page: int = 1, page_size: int = 50):
        if page < 1 or not 1 <= page_size <= 100:
            raise HTTPException(422, '分页参数无效')
        clauses, args = ['1=1'], []
        if status != 'all':
            clauses.append('active=?'); args.append(int(status == 'active'))
        if q.strip():
            clauses.append("strpos(search_text,?)>0" if catalog.postgres else "instr(seller_key(source || ' ' || seller || ' ' || reason),?)>0")
            args.append(seller_key(q))
        where = ' AND '.join(clauses)
        with catalog.connect() as db:
            total = db.execute(f'SELECT COUNT(*) FROM seller_blacklist WHERE {where}', args).fetchone()[0]
            rows = db.execute(f'''SELECT * FROM seller_blacklist WHERE {where}
                ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?''', [*args, page_size, (page-1)*page_size])
            return dict(items=[public_entry(r) for r in rows], total=total, page=page, page_size=page_size)

    @router.post('', status_code=201)
    def create(data: NewSeller, request: Request):
        now = datetime.now(timezone.utc).isoformat()
        try:
            with catalog.connect() as db:
                write_lock(db)
                entry_id = insert_id(db, '''INSERT INTO seller_blacklist
                    (source,seller,source_key,seller_key,reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?)''',
                    (data.source, data.seller, seller_key(data.source), seller_key(data.seller), data.reason, now, now))
                entry = public_entry(db.execute('SELECT * FROM seller_blacklist WHERE id=?', (entry_id,)).fetchone())
                record(db, entry, request, 'block')
                return entry
        except INTEGRITY_ERRORS:
            raise HTTPException(409, '该平台的卖家已有记录，请在名单中搜索并编辑或重新拉黑')

    @router.patch('/{entry_id}')
    def update(entry_id: int, data: SellerUpdate, request: Request):
        with catalog.connect() as db:
            write_lock(db)
            old = db.execute('SELECT * FROM seller_blacklist WHERE id=?', (entry_id,)).fetchone()
            if not old:
                raise HTTPException(404, '黑名单记录不存在')
            if old['reason'] == data.reason and bool(old['active']) == data.active:
                return public_entry(old)
            now = datetime.now(timezone.utc).isoformat()
            db.execute('UPDATE seller_blacklist SET reason=?,active=?,updated_at=? WHERE id=?',
                       (data.reason, int(data.active), now, entry_id))
            entry = public_entry(db.execute('SELECT * FROM seller_blacklist WHERE id=?', (entry_id,)).fetchone())
            action = 'edit' if old['active'] == data.active else ('block' if data.active else 'unblock')
            record(db, entry, request, action)
            return entry

    return router
