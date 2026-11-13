"""Allow supplier identities with no ASIN, retaining uniqueness for real ASINs."""
def initialize(db, postgres=False):
    db.execute('''CREATE TABLE IF NOT EXISTS integration_leases(
        name TEXT PRIMARY KEY,token TEXT NOT NULL,expires_at DOUBLE PRECISION NOT NULL)''')
    if db.execute('SELECT 1 FROM migrations WHERE version=13').fetchone():
        return
    if postgres:
        db.execute('ALTER TABLE products DROP CONSTRAINT IF EXISTS products_source_asin_key')
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS product_asin_identity ON products(source,asin) WHERE asin<>''")
    else:
        # Rebuild only the parent table, keeping every ID and all referencing rows.
        # This runs before any schema/data transaction on this connection.
        db.commit()
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute('PRAGMA legacy_alter_table=ON')
        try:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM migrations WHERE version=13').fetchone():
                db.commit()
                return
            db.execute('CREATE TABLE products_v13(id TEXT PRIMARY KEY,source TEXT NOT NULL,asin TEXT NOT NULL)')
            db.execute('INSERT INTO products_v13 SELECT id,source,asin FROM products')
            db.execute('DROP TABLE products')
            db.execute('ALTER TABLE products_v13 RENAME TO products')
            db.execute("CREATE UNIQUE INDEX product_asin_identity ON products(source,asin) WHERE asin<>''")
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('供应商身份迁移完整性检查失败')
            db.execute('INSERT INTO migrations VALUES(13)')
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.execute('PRAGMA legacy_alter_table=OFF')
            db.execute('PRAGMA foreign_keys=ON')
    if postgres:
        db.execute('INSERT INTO migrations VALUES(13)')
