"""Small DB-API boundary: bound parameters, rows, and explicit transaction locks.

SQL dialect differences belong in repository queries/schema, not in a SQL rewriter.
SQLite remains available for offline access to historical files and migrations.
"""
import re
import sqlite3

import psycopg

INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg.IntegrityError)


class Row(dict):
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


def bound_sql(query):
    """Translate only qmark placeholders outside quoted SQL literals/identifiers."""
    return re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\?",
                  lambda m: '%s' if m[0] == '?' else m[0], query.replace('%', '%%'))


class PostgresConnection:
    dialect = 'postgresql'

    def __init__(self, connection):
        self.raw = connection

    def execute(self, query, params=()):
        return self.raw.execute(bound_sql(query), tuple(params))

    def executemany(self, query, rows):
        cursor = self.raw.cursor()
        cursor.executemany(bound_sql(query), rows)
        return cursor


def is_postgres(db):
    return getattr(db, 'dialect', '') == 'postgresql'


def write_lock(db, scope='catalog'):
    """Serialize related decisions across workers, without blocking readers.

Catalog writes share a lock so batch confirmation sees consistent edits, seller
decisions and store status. Authentication has an independent lock.
"""
    if is_postgres(db):
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))', ('jiwu:' + scope,))
    else:
        db.execute('BEGIN IMMEDIATE')


def read_snapshot(db):
    db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ' if is_postgres(db) else 'BEGIN')


def insert_id(db, query, params):
    if is_postgres(db):
        return db.execute(query + ' RETURNING id', params).fetchone()[0]
    return db.execute(query, params).lastrowid
