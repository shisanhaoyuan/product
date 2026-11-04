#!/bin/sh
set -eu
umask 077
catalog_backup_dir=/www/server/jiwu-backups
mkdir -p "$catalog_backup_dir"
catalog_backup_file="$catalog_backup_dir/catalog-$(date -u +%Y%m%dT%H%M%SZ).db"
cd /www/wwwroot/jiwu
runuser -u jiwu -- .venv/bin/python -m catalog --db /www/server/jiwu-data/catalog.db backup "$catalog_backup_file"
# No automatic deletion: configure retention only after off-server backups work.
