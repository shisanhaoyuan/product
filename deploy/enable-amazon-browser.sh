#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo '请在服务器 root 终端执行'; exit 1; }
cd /www/wwwroot/jiwu
exec 9>/run/lock/jiwu-update.lock
flock -n 9 || { echo '另一个部署任务正在运行'; exit 1; }
systemctl is-active --quiet jiwu
site_host=$(.venv/bin/python - <<'PY'
import shlex
from pathlib import Path
from urllib.parse import urlsplit
for line in Path('/etc/jiwu.env').read_text().splitlines():
    parts = shlex.split(line, comments=True)
    if len(parts) == 1 and parts[0].startswith('CATALOG_PUBLIC_ORIGIN='):
        origin = urlsplit(parts[0].partition('=')[2])
        if origin.scheme == 'https' and origin.hostname and not origin.username:
            print(origin.netloc)
            break
else:
    raise SystemExit('缺少有效的 CATALOG_PUBLIC_ORIGIN')
PY
)
root=/www/server/jiwu-data/amazon-browser
runtime=$root/venv/bin/python
cache=$root/cache
install -d -m 700 -o jiwu -g jiwu "$root" "$cache"
as_app() { runuser -u jiwu -- env HOME="$root" XDG_CACHE_HOME="$cache" "$@"; }
if [[ ! -x $runtime ]]; then
  as_app .venv/bin/python -m venv "$root/venv"
fi
req=$(mktemp)
trap 'rm -f -- "$req"' EXIT
install -m 644 deploy/amazon-browser-requirements.txt "$req"
as_app "$runtime" -m pip install --no-cache-dir -r "$req"
rm -f -- "$req"
as_app "$runtime" -m pip check
as_app "$runtime" -m camoufox fetch official/158.0-beta.40
as_app "$runtime" - <<'PY'
from wraith.engine import launch
s = launch(engine='camoufox', headless=True, geoip=False)
try:
    s.page.goto('about:blank')
    assert s.page.url == 'about:blank'
finally:
    s.close()
PY
echo 'Wraith 空白页启动自检通过'
dir=/etc/systemd/system/jiwu.service.d
file=$dir/70-jiwu-amazon-browser.conf
install -d -m 755 "$dir"
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
if [[ -e $file ]]; then cp -p "$file" "$tmp/before"; fi
# EnvironmentFile entries override Environment=, including any old /etc/jiwu.env values.
conf=/etc/jiwu-amazon-browser.env
if [[ -e $conf ]]; then cp -p "$conf" "$tmp/env-before"; fi
printf 'CATALOG_AMAZON_BROWSER_PYTHON=%s\nCATALOG_AMAZON_BROWSER_ENGINE=camoufox\nCATALOG_AMAZON_BROWSER_CACHE=%s\n' "$runtime" "$cache" > "$tmp/env"
printf '[Service]\nEnvironmentFile=%s\n' "$conf" > "$tmp/unit"
install -m 600 "$tmp/env" "$conf"
install -m 644 "$tmp/unit" "$file"
healthy() {
  local pid
  pid=$(systemctl show jiwu -p MainPID --value)
  [[ $pid != 0 ]] &&
    grep -Fzxq "CATALOG_AMAZON_BROWSER_PYTHON=$runtime" "/proc/$pid/environ" &&
    curl -fsS --max-time 3 -H "Host: $site_host" http://127.0.0.1:8765/api/health >/dev/null 2>&1
}
if systemctl daemon-reload && systemctl restart jiwu; then
  for attempt in {1..20}; do
    if healthy; then echo 'Wraith 已启用，jiwu 健康检查通过'; exit 0; fi
    sleep 1
  done
fi
if [[ -e $tmp/before ]]; then cp -p "$tmp/before" "$file"; else rm -f "$file"; fi
if [[ -e $tmp/env-before ]]; then cp -p "$tmp/env-before" "$conf"; else rm -f "$conf"; fi
systemctl daemon-reload
systemctl restart jiwu
echo '启用失败，已恢复原服务配置；请查看 journalctl -u jiwu -n 30。' >&2
exit 1
