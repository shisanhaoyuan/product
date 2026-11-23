#!/usr/bin/env bash
# Update an existing installation configured by 宝塔部署说明.md.
# The function is parsed before Git changes this file on disk.
set -Eeuo pipefail

main() {
  local target_branch=main explicit_branch=0 allow_rollback=0 current_branch local_target
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --help)
        printf '%s\n' 'Usage: sudo bash deploy/update-git.sh [--branch BRANCH] [--rollback]' \
          '不带参数更新 main；--branch 部署指定分支；--rollback 允许回退到祖先版本。' \
          '更新前备份数据及代码；回退只切换代码，不还原数据库。'
        return ;;
      --branch)
        [[ $# -ge 2 && $explicit_branch == 0 ]] || { printf '%s\n' '--branch 需要且只能指定一个分支。' >&2; return 1; }
        target_branch=$2; explicit_branch=1; shift 2 ;;
      --rollback) allow_rollback=1; shift ;;
      *) printf '未知参数：%s\n' "$1" >&2; return 1 ;;
    esac
  done
  [[ $EUID == 0 ]] || { printf '%s\n' '请以 root 运行。' >&2; return 1; }
  [[ $allow_rollback == 0 || $explicit_branch == 1 ]] || { printf '%s\n' '回退时必须使用 --branch 明确指定分支。' >&2; return 1; }
  [[ $target_branch != -* ]] && git check-ref-format "refs/heads/$target_branch" || {
    printf '%s\n' '分支名称无效。' >&2; return 1;
  }
  umask 027
  local app_dir=/www/wwwroot/jiwu data_dir=/www/server/jiwu-data
  local backup_root=/www/server/jiwu-backups before_sha target_sha backup_dir origin
  local stopped=0 patrol_timer_active=0
  for executable in git node npm flock systemctl runuser curl pg_dump tar; do
    command -v "$executable" >/dev/null || { printf '缺少命令：%s\n' "$executable" >&2; return 1; }
  done
  exec 9>/run/lock/jiwu-update.lock
  flock -n 9 || { printf '%s\n' '已有更新任务运行。' >&2; return 1; }
  cd "$app_dir"
  [[ -x .venv/bin/python && -f /etc/jiwu.env ]] || {
    printf '%s\n' '本脚本仅更新已完成首次安装的服务。' >&2; return 1;
  }
  current_branch=$(git branch --show-current)
  [[ -n $current_branch ]] || { printf '%s\n' '当前为游离提交，请先确认分支。' >&2; return 1; }
  [[ $explicit_branch == 1 || $current_branch == main ]] || { printf '%s\n' '当前不是 main；请用 --branch 明确指定部署分支。' >&2; return 1; }
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
assert values.get('CATALOG_DATABASE_URL','').startswith(('postgresql://','postgres://')), '先完成 PostgreSQL 首次迁移'
assert values.get('CATALOG_IMAGE_STORAGE') == 'filesystem', '当前部署使用图片目录'
assert values.get('CATALOG_IMAGE_ROOT') == '/www/server/jiwu-data/images', '图片目录配置不符'
origin = values.get('CATALOG_PUBLIC_ORIGIN', '')
u = urlparse(origin)
assert u.scheme == 'https' and u.hostname and not u.username and not u.password and not u.query and not u.fragment and u.path in ('', '/'), '请先配置实际 HTTPS 访问地址（域名或 IP）'
assert u.hostname != 'catalog.example.com', '请替换示例域名'
print(origin.rstrip('/'))
PY
  )
  git fetch origin "$target_branch"
  before_sha=$(git rev-parse HEAD)
  target_sha=$(git rev-parse --verify 'FETCH_HEAD^{commit}')
  if ! git merge-base --is-ancestor "$before_sha" "$target_sha"; then
    [[ $allow_rollback == 1 ]] && git merge-base --is-ancestor "$target_sha" "$before_sha" || {
      printf '%s\n' '本地与远端历史已分叉或本地领先，停止更新。' >&2; return 1;
    }
  fi
  if git show-ref --verify --quiet "refs/heads/$target_branch"; then
    local_target=$(git rev-parse "refs/heads/$target_branch")
    git merge-base --is-ancestor "$local_target" "$target_sha" || {
      printf '%s\n' '本地目标分支含远端没有的提交，停止更新，未覆盖。' >&2; return 1;
    }
  fi
  install -d -m 700 -o jiwu -g jiwu "$backup_root"
  backup_dir=$(mktemp -d "$backup_root/update-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
  chown jiwu:jiwu "$backup_dir"
  printf '%s\n' "$before_sha" > "$backup_dir/previous-commit.txt"
  printf '%s\n' "$target_sha" > "$backup_dir/target-commit.txt"
  printf '%s\n' "$current_branch" > "$backup_dir/previous-branch.txt"
  printf '%s\n' "$target_branch" > "$backup_dir/target-branch.txt"
  git archive --format=tar.gz -o "$backup_dir/previous-code.tar.gz" "$before_sha"
  .venv/bin/python -m pip freeze > "$backup_dir/previous-dependencies.txt"
  if [[ -d web/dist ]]; then tar -czf "$backup_dir/previous-frontend.tar.gz" -C web dist; fi
  trap 'if [[ $stopped == 1 ]]; then systemctl stop jiwu || true; printf "更新未完成，服务已停止。备份：%s；原提交：%s。请检查错误后再启动，勿直接覆盖数据库。\n" "$backup_dir" "$before_sha" >&2; fi' EXIT
  # Stop before backup so the backup includes the last accepted write.
  stopped=1
  if systemctl is-active --quiet jiwu-giga-patrol.timer; then
    patrol_timer_active=1
    systemctl stop jiwu-giga-patrol.timer
  fi
  if systemctl cat jiwu-giga-patrol.service >/dev/null 2>&1; then
    systemctl stop jiwu-giga-patrol.service
  fi
  systemctl stop jiwu
  bash deploy/backup.sh > "$backup_dir/data-backup.txt"
  cat "$backup_dir/data-backup.txt"
  if [[ $current_branch == "$target_branch" ]]; then
    git merge --ff-only "$target_sha"
  elif git show-ref --verify --quiet "refs/heads/$target_branch"; then
    git switch "$target_branch"
    git merge --ff-only "$target_sha"
  else
    git switch --no-track -c "$target_branch" "$target_sha"
  fi
  if git show-ref --verify --quiet "refs/remotes/origin/$target_branch"; then
    git branch --set-upstream-to="origin/$target_branch" "$target_branch"
  fi
  .venv/bin/python -m pip install -r deploy/requirements.lock
  .venv/bin/python -m pip install --no-deps -e .
  .venv/bin/python -m pip check
  (cd frontend && npm ci --include=dev && npm run build)
  chgrp -R jiwu catalog web browser-helper .venv
  chmod -R g+rX catalog web browser-helper .venv
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
  # Keep an already-installed weekly task current; do not enable it implicitly.
  if [[ -f /etc/systemd/system/jiwu-giga-patrol.service ]]; then
    install -m 644 deploy/jiwu-giga-patrol.service /etc/systemd/system/jiwu-giga-patrol.service
    install -m 644 deploy/jiwu-giga-patrol.timer /etc/systemd/system/jiwu-giga-patrol.timer
    systemctl daemon-reload
    if [[ $patrol_timer_active == 1 ]]; then systemctl start jiwu-giga-patrol.timer; fi
  fi
  stopped=0
  trap - EXIT
  printf '更新完成（分支 %s）：%s → %s\n备份：%s\n请登录 %s 核对商品及图片。\n' "$target_branch" "$before_sha" "$target_sha" "$backup_dir" "$origin"
}

main "$@"
