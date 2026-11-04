#!/usr/bin/env bash
# Update an existing installation configured by 宝塔部署说明.md.
# The function is parsed before Git changes this file on disk.
set -Eeuo pipefail

main() {
  if [[ ${1:-} == --help ]]; then
    printf '%s\n' 'Usage: sudo bash /www/wwwroot/jiwu/deploy/update-git.sh' \
      'Updates an existing main-branch installation; backs up data before pulling.'
    return
  fi
  [[ $# == 0 && $EUID == 0 ]] || { printf '%s\n' '请以 root 运行，不带参数。' >&2; return 1; }
  umask 027
  local app_dir=/www/wwwroot/jiwu data_dir=/www/server/jiwu-data
  local backup_root=/www/server/jiwu-backups before_sha target_sha backup_dir origin
  local stopped=0
  for executable in git node npm flock systemctl runuser curl; do
    command -v "$executable" >/dev/null || { printf '缺少命令：%s\n' "$executable" >&2; return 1; }
  done
  exec 9>/run/lock/jiwu-update.lock
  flock -n 9 || { printf '%s\n' '已有更新任务运行。' >&2; return 1; }
  cd "$app_dir"
  [[ -x .venv/bin/python && -f "$data_dir/catalog.db" && -f /etc/jiwu.env ]] || {
    printf '%s\n' '本脚本仅更新已完成首次安装的服务。' >&2; return 1;
  }
  [[ $(git branch --show-current) == main ]] || { printf '%s\n' '当前不是 main 分支，停止更新。' >&2; return 1; }
  [[ -z $(git status --porcelain) ]] || { printf '%s\n' '服务器有本地代码修改，请先处理，未覆盖。' >&2; return 1; }
  node -e 'const [m,n]=process.versions.node.split(".").map(Number); if(!((m===22&&n>=12)||m>=24)) {console.error("需要 Node 22.12+ 或 24+");process.exit(1)}'
  origin=$(.venv/bin/python - <<'PY'
import shlex
from pathlib import Path
from urllib.parse import urlparse
values = {}
for line in Path('/etc/jiwu.env').read_text().splitlines():
    tokens = shlex.split(line, comments=True)
    if len(tokens) == 1 and '=' in tokens[0]:
        k, v = tokens[0].split('=', 1)
        values[k] = v
assert values.get('CATALOG_AUTH_ENABLED') == '1', '团队登录必须启用'
assert values.get('CATALOG_COOKIE_SECURE') == '1', '需使用 HTTPS Cookie'
assert values.get('CATALOG_DB') == '/www/server/jiwu-data/catalog.db', '数据库路径与部署配置不符'
origin = values.get('CATALOG_PUBLIC_ORIGIN', '')
u = urlparse(origin)
assert u.scheme == 'https' and u.hostname and not u.username and not u.password and not u.query and not u.fragment and u.path in ('', '/'), '请先配置实际 HTTPS 域名'
assert u.hostname != 'catalog.example.com', '请替换示例域名'
print(origin.rstrip('/'))
PY
  )
  git fetch origin main
  before_sha=$(git rev-parse HEAD)
  target_sha=$(git rev-parse origin/main)
  git merge-base --is-ancestor "$before_sha" "$target_sha" || {
    printf '%s\n' '本地与远端历史已分叉或本地领先，停止更新。' >&2; return 1;
  }
  install -d -m 700 -o jiwu -g jiwu "$backup_root"
  backup_dir=$(mktemp -d "$backup_root/update-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
  chown jiwu:jiwu "$backup_dir"
  printf '%s\n' "$before_sha" > "$backup_dir/previous-commit.txt"
  printf '%s\n' "$target_sha" > "$backup_dir/target-commit.txt"
  git archive --format=tar.gz -o "$backup_dir/previous-code.tar.gz" "$before_sha"
  .venv/bin/python -m pip freeze > "$backup_dir/previous-dependencies.txt"
  if [[ -d web/dist ]]; then tar -czf "$backup_dir/previous-frontend.tar.gz" -C web dist; fi
  trap 'if [[ $stopped == 1 ]]; then systemctl stop jiwu || true; printf "更新未完成，服务已停止。备份：%s；原提交：%s。请检查错误后再启动，勿直接覆盖数据库。\n" "$backup_dir" "$before_sha" >&2; fi' EXIT
  # Stop before backup so the backup includes the last accepted write.
  stopped=1
  systemctl stop jiwu
  runuser -u jiwu -- .venv/bin/python -m catalog --db "$data_dir/catalog.db" backup "$backup_dir/catalog.db"
  git merge --ff-only "$target_sha"
  .venv/bin/python -m pip install -r deploy/requirements.lock
  .venv/bin/python -m pip install --no-deps -e .
  .venv/bin/python -m pip check
  (cd frontend && npm ci --include=dev && npm run build)
  chgrp -R jiwu catalog web .venv
  chmod -R g+rX catalog web .venv
  systemctl start jiwu
  local healthy=0
  for attempt in {1..20}; do
    if curl --fail --silent --show-error --max-time 3 -H "Host: ${origin#https://}" http://127.0.0.1:8765/api/health > "$backup_dir/health.json" 2>/dev/null; then
      healthy=1
      break
    fi
    sleep 1
  done
  if [[ $healthy != 1 ]] || ! systemctl is-active --quiet jiwu; then
    printf '%s\n' '服务健康检查失败。' >&2
    exit 1
  fi
  stopped=0
  trap - EXIT
  printf '更新完成：%s → %s\n备份：%s\n请登录 %s 核对商品及图片。\n' "$before_sha" "$target_sha" "$backup_dir" "$origin"
}

main "$@"
