"""Transactional repository; upstream IDs are scoped by adapter namespace."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .models import ImportBundle
from .categories import CATEGORY_SQL, category_option
from .blacklist import initialize, seller_key, BLOCKED_SQL, active_entries, matches
from .editing import initialize as initialize_editing, register as register_editing
from .database import PostgresConnection, row_factory, write_lock, read_snapshot


def encoded(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Catalog:
    def __init__(self, path):
        self.postgres = str(path).startswith(('postgresql://', 'postgres://'))
        if self.postgres:
            self.path = str(path)
            with self.connect() as db:
                write_lock(db, 'schema')
                db.raw.execute(Path(__file__).with_name('postgres.sql').read_text())
                from .prelisting import initialize as initialize_prelisting
                initialize_prelisting(db)
                if not db.execute('SELECT 1 FROM migrations WHERE version=7').fetchone():
                    from .projection import rebuild
                    rebuild(db)
                db.executemany('INSERT INTO migrations VALUES(?) ON CONFLICT DO NOTHING',
                               [(v,) for v in (1,2,3,5,6,7)])
            return
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO migrations VALUES(1);
                CREATE TABLE IF NOT EXISTS products(
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, asin TEXT NOT NULL,
                    UNIQUE(source, asin));
                CREATE TABLE IF NOT EXISTS listings(
                    id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
                    namespace TEXT NOT NULL, external_id TEXT NOT NULL,
                    sku TEXT NOT NULL, store TEXT NOT NULL, brand TEXT NOT NULL,
                    template TEXT NOT NULL, uploaded_at TEXT NOT NULL,
                    observed_at TEXT NOT NULL, evidence_kind TEXT NOT NULL,
                    saved INTEGER NOT NULL, complete INTEGER NOT NULL, verified INTEGER NOT NULL,
                    search_text TEXT NOT NULL, data_json TEXT NOT NULL, digest TEXT NOT NULL,
                    UNIQUE(namespace, external_id));
                CREATE INDEX IF NOT EXISTS listing_store ON listings(store,uploaded_at);
                CREATE TABLE IF NOT EXISTS variants(
                    listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
                    sku TEXT NOT NULL, asin TEXT NOT NULL, data_json TEXT NOT NULL,
                    PRIMARY KEY(listing_id,sku));
                CREATE INDEX IF NOT EXISTS variant_asin ON variants(asin);
                CREATE TABLE IF NOT EXISTS snapshots(
                    id INTEGER PRIMARY KEY, listing_id TEXT NOT NULL REFERENCES listings(id),
                    digest TEXT NOT NULL, observed_at TEXT NOT NULL,
                    evidence_kind TEXT NOT NULL, data_json TEXT NOT NULL,
                    UNIQUE(listing_id,digest));
                CREATE TABLE IF NOT EXISTS imports(
                    id INTEGER PRIMARY KEY, at TEXT NOT NULL, namespace TEXT NOT NULL,
                    label TEXT NOT NULL, digest TEXT NOT NULL, result_json TEXT NOT NULL);
            """)

            initialize(db)
            initialize_editing(db)
            from .prelisting import initialize as initialize_prelisting
            initialize_prelisting(db)
            from .images import initialize as initialize_images
            initialize_images(db)

    @contextmanager
    def connect(self):
        if self.postgres:
            import psycopg
            with psycopg.connect(self.path, row_factory=row_factory, connect_timeout=10) as connection:
                connection.execute("SET LOCAL lock_timeout='20s'")
                connection.execute("SET LOCAL statement_timeout='120s'")
                yield PostgresConnection(connection)
            return
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.create_function("seller_key", 1, seller_key, deterministic=True)
        register_editing(db)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, bundle: ImportBundle, actor_id=None):
        result = dict(created=0, updated=0, unchanged=0, older_skipped=0, snapshots_added=0)
        seen = set()
        rank = {"source_only": 0, "editor_snapshot": 1, "persisted_readback": 2}
        with self.connect() as db:
            write_lock(db)
            for item in bundle.listings:
                if item.external_id in seen:
                    raise ValueError("同一次导入存在重复 external_id")
                seen.add(item.external_id)
                data = item.model_dump()
                raw = encoded(data)
                digest = hashlib.sha256(raw.encode()).hexdigest()
                lid = hashlib.sha256(f"{bundle.namespace}:{item.external_id}".encode()).hexdigest()[:24]
                pid = hashlib.sha256(f"{item.source}:{item.asin}".encode()).hexdigest()[:24]
                old = db.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
                if old:
                    prev = json.loads(old["data_json"])
                    if any(prev[k] != data[k] for k in ("source", "asin", "sku", "store", "template", "record_id")):
                        raise ValueError(f"商品身份冲突：{item.external_id}；不能改变 ASIN、SKU、店铺或模板")
                    prior_variants = {v['sku']: v for v in prev['variants']}
                    for variant in data['variants']:
                        prior = prior_variants.get(variant['sku'])
                        if prior and prior['asin'] and variant['asin'] != prior['asin']:
                            raise ValueError(f"子 SKU 身份冲突：{variant['sku']}；不能更换或清空已确认的 ASIN")
                    if old["digest"] == digest:
                        result["unchanged"] += 1
                        continue
                    if datetime.fromisoformat(item.observed_at) < datetime.fromisoformat(old["observed_at"]) or (
                        rank[item.evidence_kind] < rank[old["evidence_kind"]]
                    ):
                        result["older_skipped"] += 1
                        continue
                    if datetime.fromisoformat(item.observed_at) == datetime.fromisoformat(old["observed_at"]):
                        raise ValueError(f"同一采集时间内容冲突：{item.external_id}；请使用真实的新采集时间")
                db.execute("INSERT INTO products VALUES(?,?,?) ON CONFLICT DO NOTHING", (pid,item.source,item.asin))
                search = " ".join([item.asin,item.sku,item.platform_sku,item.copy.title,item.seller,
                                   item.store,item.brand,item.creator,item.template,item.source,
                                   *[f"{v.asin} {v.sku} {v.seller}" for v in item.variants]]).casefold()
                db.execute("""INSERT INTO listings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET product_id=excluded.product_id,
                    brand=excluded.brand, uploaded_at=excluded.uploaded_at,
                    observed_at=excluded.observed_at, evidence_kind=excluded.evidence_kind,
                    saved=excluded.saved,complete=excluded.complete,verified=excluded.verified,
                    search_text=excluded.search_text,data_json=excluded.data_json,digest=excluded.digest""",
                    (lid,pid,bundle.namespace,item.external_id,item.sku,item.store,item.brand,item.template,
                     item.uploaded_at,item.observed_at,item.evidence_kind,int(item.save_succeeded),
                     int(item.fill_complete),int(item.readback_verified),search,raw,digest))
                db.execute("DELETE FROM variants WHERE listing_id=?", (lid,))
                db.executemany("INSERT INTO variants VALUES(?,?,?,?)", [
                    (lid,v.sku,v.asin,encoded(v.model_dump())) for v in item.variants])
                cur = db.execute("INSERT INTO snapshots(listing_id,digest,observed_at,evidence_kind,data_json) VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING",
                                 (lid,digest,item.observed_at,item.evidence_kind,raw))
                result["snapshots_added"] += cur.rowcount
                result["updated" if old else "created"] += 1
                self.refresh(db, lid)
            db.execute("INSERT INTO imports(at,namespace,label,digest,result_json) VALUES(?,?,?,?,?)",
                       (datetime.now(timezone.utc).isoformat(),bundle.namespace,bundle.label,
                        hashlib.sha256(encoded(bundle.model_dump()).encode()).hexdigest(),encoded(result)))
            if actor_id is not None:
                db.execute('INSERT INTO audit_events(at,actor_id,action,target) VALUES(?,?,?,?)',
                           (int(datetime.now(timezone.utc).timestamp()),actor_id,'catalog.import',bundle.namespace))
        return result

    def refresh(self, db, lid):
        if self.postgres:
            from .projection import refresh
            refresh(db, lid)

    @property
    def category_sql(self):
        return 'category' if self.postgres else CATEGORY_SQL

    def stats(self):
        with self.connect() as db:
            read_snapshot(db)
            counts = dict(db.execute("""SELECT COUNT(*) listings,COALESCE(SUM(saved),0) saved,
                COALESCE(SUM(complete),0) complete,COALESCE(SUM(verified),0) verified,
                COALESCE(SUM(CASE WHEN evidence_kind='persisted_readback' THEN 1 ELSE 0 END),0) reopened FROM current_listings""").fetchone())
            counts["products"] = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            counts["variants"] = db.execute("SELECT COUNT(*) FROM variants").fetchone()[0]
            counts["stores"] = [dict(r) for r in db.execute("SELECT store,COUNT(*) count FROM current_listings GROUP BY store ORDER BY store")]
            counts["brands"] = [r[0] for r in db.execute("SELECT DISTINCT brand FROM current_listings ORDER BY brand")]
            counts["dates"] = [r[0] for r in db.execute("SELECT DISTINCT substr(uploaded_at,1,10) AS day FROM listings ORDER BY day DESC")]
            counts["categories"] = [category_option(row['category']) | {'count': row['count']}
                for row in db.execute(f"SELECT category,COUNT(*) AS count FROM (SELECT {self.category_sql} AS category FROM current_listings) c GROUP BY category ORDER BY category='' ASC,lower(category)")]
            counts["review"] = counts["listings"] - counts["complete"]
            return counts

    def filter_sql(self, q="", store="", brand="", status="", date="", category="", seller_status=""):
        clauses, args = ["1=1"], []
        if q:
            clauses.append(f"{'strpos' if self.postgres else 'instr'}(search_text,?)>0"); args.append(q.casefold())
        for key, value in (("store",store),("brand",brand)):
            if value:
                clauses.append(f"{key}=?"); args.append(value)
        if date:
            clauses.append("substr(uploaded_at,1,10)=?"); args.append(date)
        if status in {"complete","review","saved","unconfirmed","reopened","snapshot"}:
            clauses.append({"complete":"complete=1","review":"complete=0","saved":"saved=1",
                "unconfirmed":"saved=0","reopened":"evidence_kind='persisted_readback'",
                "snapshot":"evidence_kind!='persisted_readback'"}[status])
        if seller_status in {"blocked", "unblocked"}:
            blocked = """EXISTS (SELECT 1 FROM current_listing_sellers s JOIN seller_blacklist b
                ON b.source_key=s.source_key AND b.seller_key=s.seller_key
                WHERE b.active=1 AND s.listing_id=listings.id)""" if self.postgres else BLOCKED_SQL
            clauses.append(("NOT " if seller_status == "unblocked" else "") + blocked)
        where = " AND ".join(clauses)
        if category:
            if category == 'missing' or category.startswith('type:'):
                where += f" AND ({self.category_sql})=?"
                args.append('' if category == 'missing' else category[5:])
            else:
                where += ' AND 0=1'
        return where, args

    def find(self, q="", store="", brand="", status="", date="", page=1, page_size=24, category="", seller_status=""):
        where, args = self.filter_sql(q,store,brand,status,date,category,seller_status)
        with self.connect() as db:
            read_snapshot(db)
            total = db.execute(f"SELECT COUNT(*) FROM current_listings AS listings WHERE {where}",args).fetchone()[0]
            rows = db.execute(f"SELECT id,data_json,{self.category_sql} AS category FROM current_listings AS listings WHERE {where} ORDER BY uploaded_at DESC,id LIMIT ? OFFSET ?",
                              [*args,page_size,(page-1)*page_size]).fetchall()
            entries = active_entries(db)
            items=[]
            for r in rows:
                d=json.loads(r["data_json"])
                prices=[v.get('price') for v in d['variants']] if d['variants'] else [d['price']]
                prices=[p for p in prices if p not in (None,'')]
                items.append({k:d[k] for k in ('asin','sku','source','source_url','seller','store','brand',
                    'template','creator','uploaded_at','observed_at','evidence_kind','save_succeeded',
                    'fill_complete','readback_verified','local_edit')} | dict(id=r['id'],title=d['copy']['title'],
                    image=d['images'][0] if d['images'] else next((v['images'][0] for v in d['variants'] if v['images']),''),
                    variant_count=len(d['variants']),prices=prices,issue_count=len(d['issues']),category=r['category'],seller_blacklist=matches(d, entries)))
            return dict(items=items,total=total,page=page,page_size=page_size)

    def get(self, listing_id):
        with self.connect() as db:
            read_snapshot(db)
            r=db.execute(f"SELECT data_json,{self.category_sql} AS category FROM current_listings WHERE id=?",(listing_id,)).fetchone()
            if not r:return None
            return json.loads(r[0]) | dict(id=listing_id,category=r['category'],
                seller_blacklist=matches(json.loads(r[0]), active_entries(db)),history=[dict(h) for h in db.execute(
                "SELECT id,observed_at,evidence_kind,digest FROM snapshots WHERE listing_id=? ORDER BY observed_at DESC",(listing_id,))])

    def imports(self):
        with self.connect() as db:
            return [dict(r) | {'result':json.loads(r['result_json'])} for r in db.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 100")]

    def export(self):
        with self.connect() as db:
            return [dict(namespace=r['namespace'],id=r['id'],**json.loads(r['data_json'])) for r in db.execute("SELECT id,namespace,data_json FROM listings ORDER BY uploaded_at DESC")]
