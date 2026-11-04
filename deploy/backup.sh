#!/usr/bin/env bash
# Consistent PostgreSQL + filesystem backup. Run as root; no deletion/retention.
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo '请以 root 运行。' >&2; exit 1; }
umask 077
exec 8>/run/lock/jiwu-backup.lock
flock -n 8 || { echo '已有备份任务运行。' >&2; exit 1; }
cd /www/wwwroot/jiwu
.venv/bin/python - <<'PY'
import os
from catalog.config import load_env_file
load_env_file('/etc/jiwu.env')
assert os.environ.get('CATALOG_DATABASE_URL','').startswith(('postgresql://','postgres://')), '本脚本要求 PostgreSQL'
assert os.environ.get('CATALOG_IMAGE_STORAGE') == 'filesystem', '本脚本要求服务器图片目录'
assert os.environ.get('CATALOG_IMAGE_ROOT') == '/www/server/jiwu-data/images', '图片路径与部署约定不符'
PY
command -v pg_dump >/dev/null
install -d -m 700 -o jiwu -g jiwu /www/server/jiwu-backups
backup_dir=$(mktemp -d /www/server/jiwu-backups/full-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX)
chown jiwu:jiwu "$backup_dir"
was_active=0
systemctl is-active --quiet jiwu && was_active=1
trap 'result=$?; if [[ $was_active == 1 ]]; then systemctl start jiwu || result=1; fi; if [[ $result != 0 ]]; then echo "备份未完成：$backup_dir" >&2; fi; exit "$result"' EXIT
if [[ $was_active == 1 ]]; then systemctl stop jiwu; fi
runuser -u jiwu -- .venv/bin/python -m catalog --env-file /etc/jiwu.env backup "$backup_dir/catalog.dump"
tar -czf "$backup_dir/images.tar.gz" -C /www/server/jiwu-data images
(cd "$backup_dir" && sha256sum catalog.dump images.tar.gz > SHA256SUMS)
git rev-parse HEAD > "$backup_dir/commit.txt"
touch "$backup_dir/COMPLETE"
printf '数据库和图片备份完成：%s\n请另行复制到其他机器或独立备份存储。\n' "$backup_dir"
