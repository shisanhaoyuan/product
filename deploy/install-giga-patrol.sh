#!/usr/bin/env bash
# Opt-in weekly server scheduler, independent of browser/Codex sessions.
set -euo pipefail
[[ $EUID == 0 ]] || { echo '请在服务器以 root 执行。'; exit 1; }
app_dir=/www/wwwroot/jiwu
[[ -x "$app_dir/.venv/bin/python" && -f /etc/jiwu.env ]] || { echo '请先完成商品库部署。'; exit 1; }
cd "$app_dir"
runuser -u jiwu -- .venv/bin/python - <<'PY'
from catalog.config import load_env_file
from catalog.giga import GigaSettings
load_env_file('/etc/jiwu.env')
if not GigaSettings().configured:
    raise SystemExit('请先在 /etc/jiwu.env 配置大建云仓 API。定时器尚未安装。')
PY
install -m 644 deploy/jiwu-giga-patrol.service /etc/systemd/system/jiwu-giga-patrol.service
install -m 644 deploy/jiwu-giga-patrol.timer /etc/systemd/system/jiwu-giga-patrol.timer
systemctl daemon-reload
systemctl enable --now jiwu-giga-patrol.timer
systemctl list-timers --all jiwu-giga-patrol.timer --no-pager
