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
                from .supplier_schema import initialize as initialize_suppliers
                initialize_suppliers(db, True)
                from .giga_patrol import initialize as initialize_patrol
                initialize_patrol(db)
                from .prelisting import initialize as initialize_prelisting
                initialize_prelisting(db)
                if not db.execute('SELECT 1 FROM migrations WHERE version=7').fetchone():
                    from .projection import rebuild
                    rebuild(db)
                db.executemany('INSERT INTO migrations VALUES(?) ON CONFLICT DO NOTHING',
                               [(v,) for v in (1,2,3,5,6,7)])
                from .source_capture import initialize as initialize_source_capture
                initialize_source_capture(db)
                if not db.execute('SELECT 1 FROM migrations WHERE version=12').fetchone():
                    from .projection import rebuild
                    rebuild(db)
                    db.execute('INSERT INTO migrations VALUES(12) ON CONFLICT DO NOTHING')
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

            from .supplier_schema import initialize as initialize_suppliers
            initialize_suppliers(db)
            from .giga_patrol import initialize as initialize_patrol
            initialize_patrol(db)
            initialize(db)
            initialize_editing(db)
            from .prelisting import initialize as initialize_prelisting
            initialize_prelisting(db)
            from .images import initialize as initialize_images
            initialize_images(db)
            from .source_capture import initialize as initialize_source_capture
            initialize_source_capture(db)
            db.execute('INSERT OR IGNORE INTO migrations VALUES(12)')

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
            from .import_history import context_data, previous_result
            context = context_data(bundle.import_context, actor_id)
            bundle_digest = hashlib.sha256(encoded(bundle.model_dump()).encode()).hexdigest()
            if context:
                previous = previous_result(db, self.postgres, context, bundle_digest)
                if previous is not None:
                    return previous
            for item in bundle.listings:
                if item.external_id in seen:
                    raise ValueError("同一次导入存在重复 external_id")
                seen.add(item.external_id)
                data = item.model_dump()
                raw = encoded(data)
                digest = hashlib.sha256(raw.encode()).hexdigest()
                lid = hashlib.sha256(f"{bundle.namespace}:{item.external_id}".encode()).hexdigest()[:24]
                identity = f"{bundle.namespace}:{item.sku}" if item.source == "GigaB2B" and not item.asin else item.asin
                pid = hashlib.sha256(f"{item.source}:{identity}".encode()).hexdigest()[:24]
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
                        bundle_digest,encoded(result | ({'_context': context} if context else {}))))
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

    def stats(self, source=""):
        # Filter every metric and option from the same source-scoped relation.
        where, args = self.filter_sql(source=source)
        relation = f"(SELECT listings.* FROM current_listings AS listings WHERE {where})"
        with self.connect() as db:
            read_snapshot(db)
            counts = dict(db.execute(f"""SELECT COUNT(*) listings,COALESCE(SUM(saved),0) saved,
                COALESCE(SUM(complete),0) complete,COALESCE(SUM(verified),0) verified,
                COALESCE(SUM(CASE WHEN evidence_kind='persisted_readback' THEN 1 ELSE 0 END),0) reopened
                FROM {relation} scoped""", args).fetchone())
            counts["products"] = db.execute(f"SELECT COUNT(DISTINCT product_id) FROM {relation} scoped", args).fetchone()[0]
            counts["variants"] = db.execute(f"SELECT COUNT(*) FROM variants WHERE listing_id IN (SELECT id FROM {relation} scoped)", args).fetchone()[0]
            counts["stores"] = [dict(r) for r in db.execute(f"SELECT store,COUNT(*) count FROM {relation} scoped GROUP BY store ORDER BY store", args)]
            counts["brands"] = [r[0] for r in db.execute(f"SELECT DISTINCT brand FROM {relation} scoped ORDER BY brand", args)]
            counts["dates"] = [r[0] for r in db.execute(f"SELECT DISTINCT substr(uploaded_at,1,10) AS day FROM {relation} scoped ORDER BY day DESC", args)]
            counts["categories"] = [category_option(row['category']) | {'count': row['count']}
                for row in db.execute(f"SELECT category,COUNT(*) AS count FROM (SELECT {self.category_sql} AS category FROM {relation} scoped) c GROUP BY category ORDER BY category='' ASC,lower(category)", args)]
            counts["review"] = counts["listings"] - counts["complete"]
            counts["unique_counts"] = dict(db.execute(f"""SELECT
                COUNT(DISTINCT COALESCE(NULLIF(p.asin,''),l.id)) AS all_products,
                COUNT(DISTINCT CASE WHEN l.complete=1 THEN COALESCE(NULLIF(p.asin,''),l.id) END) AS complete,
                COUNT(DISTINCT CASE WHEN l.complete=0 THEN COALESCE(NULLIF(p.asin,''),l.id) END) AS review
                FROM {relation} l JOIN products p ON p.id=l.product_id""", args).fetchone())
            return counts

    def filter_sql(self, q="", store="", brand="", status="", date="", category="", seller_status="",
                   listing_count_min=None, listing_count_max=None, source=""):
        clauses, args = ["1=1"], []
        if source:
            clauses.append("listings.product_id IN (SELECT id FROM products WHERE source=?)")
            args.append(source)
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
        if listing_count_min is not None or listing_count_max is not None:
            # Same global ASIN/template count as listing_coverage, before other filters
            # and pagination. Missing ASINs keep their individual count of one.
            count_sql = """(CASE WHEN (SELECT asin FROM products WHERE id=listings.product_id)=''
                THEN 1 ELSE (SELECT COUNT(*) FROM listings counted
                    JOIN products counted_product ON counted_product.id=counted.product_id
                    WHERE counted_product.asin=(SELECT asin FROM products WHERE id=listings.product_id)) END)"""
            for value, operator in ((listing_count_min, '>='), (listing_count_max, '<=')):
                if value is not None:
                    clauses.append(f'{count_sql}{operator}?')
                    args.append(value)
        where = " AND ".join(clauses)
        if category:
            if category == 'missing' or category.startswith('type:'):
                where += f" AND ({self.category_sql})=?"
                args.append('' if category == 'missing' else category[5:])
            else:
                where += ' AND 0=1'
        return where, args

    def selection_sql(self, *, deduplicate=False, **filters):
        where, args = self.filter_sql(**filters)
        query = f"SELECT listings.* FROM current_listings AS listings WHERE {where}"
        if deduplicate:
            # Select a matching template before pagination, retaining every source record.
            query = f"""SELECT * FROM (
                SELECT listings.*, ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(NULLIF(products.asin,''),listings.id)
                    ORDER BY listings.uploaded_at DESC,listings.id) AS asin_position
                FROM current_listings AS listings JOIN products ON products.id=listings.product_id
                WHERE {where}) AS grouped_listings WHERE asin_position=1"""
        return query, args

    def find(self, q="", store="", brand="", status="", date="", page=1, page_size=24, category="", seller_status="", deduplicate=False,
             listing_count_min=None, listing_count_max=None, source=""):
        query, args = self.selection_sql(q=q,store=store,brand=brand,status=status,date=date,
                                         category=category,seller_status=seller_status,deduplicate=deduplicate,
                                         listing_count_min=listing_count_min,listing_count_max=listing_count_max,source=source)
        with self.connect() as db:
            read_snapshot(db)
            total = db.execute(f"SELECT COUNT(*) FROM ({query}) AS listings",args).fetchone()[0]
            rows = db.execute(f"SELECT id,data_json,{self.category_sql} AS category FROM ({query}) AS listings ORDER BY uploaded_at DESC,id LIMIT ? OFFSET ?",
                              [*args,page_size,(page-1)*page_size]).fetchall()
            asins = list({json.loads(row['data_json'])['asin'] for row in rows} - {''})
            coverage = {}
            if asins:
                placeholders = ','.join('?' for _ in asins)
                for row in db.execute(f"""SELECT p.asin,l.store,COUNT(*) AS count FROM listings l
                        JOIN products p ON p.id=l.product_id WHERE p.asin IN ({placeholders})
                        GROUP BY p.asin,l.store ORDER BY l.store""", asins):
                    coverage.setdefault(row['asin'], []).append(dict(store=row['store'],count=row['count']))
            entries = active_entries(db)
            items=[]
            for r in rows:
                d=json.loads(r["data_json"])
                prices=[v.get('price') for v in d['variants']] if d['variants'] else [d['price']]
                prices=[p for p in prices if p not in (None,'')]
                review = d.get('manual_review') or {}
                items.append({k:d[k] for k in ('asin','sku','source','source_url','seller','store','brand',
                    'template','creator','uploaded_at','observed_at','evidence_kind','save_succeeded',
                    'fill_complete','readback_verified','local_edit')} | dict(manual_review=d.get('manual_review'),id=r['id'],title=d['copy']['title'],
                    image=d['images'][0] if d['images'] else next((v['images'][0] for v in d['variants'] if v['images']),''),
                    variant_count=len(d['variants']),prices=prices,currency=(d.get('source_facts',{}).get('currency') or '') if d['source']=='GigaB2B' else 'USD',issue_count=0 if review.get('valid') and review.get('complete') else len(d['issues']),category=r['category'],seller_blacklist=matches(d, entries),
                    listing_count=sum(s['count'] for s in coverage.get(d['asin'], [])) or 1,
                    listing_stores=coverage.get(d['asin'], [dict(store=d['store'],count=1)])))
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
        from .import_history import grouped_imports
        with self.connect() as db:
            return grouped_imports(db, self.postgres)

    def export(self):
        with self.connect() as db:
            return [dict(namespace=r['namespace'],id=r['id'],**json.loads(r['data_json'])) for r in db.execute("SELECT id,namespace,data_json FROM listings ORDER BY uploaded_at DESC")]
