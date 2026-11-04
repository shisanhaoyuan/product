"""Database backups; PostgreSQL stores object metadata, image files are backed up separately."""
import os
import sqlite3
import subprocess
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict


def backup_database(catalog, destination):
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if catalog.postgres:
        for key, value in conninfo_to_dict(catalog.path).items():
            env['PG' + ('DATABASE' if key == 'dbname' else key.upper())] = value
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if catalog.postgres:
            with os.fdopen(fd, 'wb') as out:
                subprocess.run(['pg_dump','--format=custom','--no-owner','--no-acl'],
                               env=env,stdout=out,stderr=subprocess.PIPE,check=True)
        else:
            os.close(fd)
            with catalog.connect() as source, sqlite3.connect(destination) as target:
                source.backup(target)
    except Exception:
        destination.unlink(missing_ok=True)
        raise RuntimeError('数据库备份失败；请检查 pg_dump 版本、连接及权限（未输出凭据）') from None
    return destination
