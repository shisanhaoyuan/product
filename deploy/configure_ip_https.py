"""Install an IP HTTPS vhost and renewal timer after Certbot has issued jiwu-ip.

Run as root on the BaoTa host: python3.11 deploy/configure_ip_https.py PUBLIC_IP
Existing site files are never overwritten. Re-running identical configuration is safe.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse


def configuration(ip, nginx):
    host = f'[{ip}]' if ':' in ip else ip
    return {
        Path('/www/server/panel/vhost/nginx/jiwu_https.conf'): (f'''# Managed by jiwu deploy/configure_ip_https.py
server {{
    listen 443 ssl;
    server_name {host};
    ssl_certificate /etc/letsencrypt/live/jiwu-ip/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/jiwu-ip/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 21m;
    location / {{
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_connect_timeout 5s;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
        proxy_cache off;
    }}
    access_log /www/wwwlogs/jiwu_https.log;
    error_log /www/wwwlogs/jiwu_https.error.log;
}}
''', 0o644),
        Path('/usr/local/sbin/jiwu-cert-reload'): (f'''#!/bin/sh
# Managed by jiwu deploy/configure_ip_https.py
set -eu
{shlex.quote(nginx)} -t
{shlex.quote(nginx)} -s reload
''', 0o755),
        Path('/etc/systemd/system/jiwu-cert-renew.service'): ('''# Managed by jiwu deploy/configure_ip_https.py
[Unit]
Description=Renew Jiwu IP HTTPS certificate
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/jiwu-certbot/bin/certbot renew --cert-name jiwu-ip --quiet --deploy-hook /usr/local/sbin/jiwu-cert-reload
TimeoutStartSec=1800
UMask=0077
''', 0o644),
        Path('/etc/systemd/system/jiwu-cert-renew.timer'): ('''# Managed by jiwu deploy/configure_ip_https.py
[Unit]
Description=Check Jiwu IP certificate renewal every six hours

[Timer]
OnCalendar=*-*-* 00,06,12,18:00:00
RandomizedDelaySec=300
Persistent=true
Unit=jiwu-cert-renew.service

[Install]
WantedBy=timers.target
''', 0o644),
    }


def run(*args):
    subprocess.run(args, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ip', type=ipaddress.ip_address)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('请以 root 执行')
    ip = str(args.ip)
    if args.ip.version != 4:
        parser.error('当前宝塔部署脚本仅支持 IPv4')
    nginx = shutil.which('nginx')
    if not nginx:
        parser.error('找不到 nginx')
    for command in ('openssl', 'curl', 'systemctl', 'systemd-analyze'):
        if not shutil.which(command):
            parser.error(f'找不到 {command}')
    certbot = '/opt/jiwu-certbot/bin/certbot'
    if not os.access(certbot, os.X_OK):
        parser.error('请先安装独立 Certbot')
    env = {}
    for line in Path('/etc/jiwu.env').read_text().splitlines():
        parts = shlex.split(line, comments=True)
        if len(parts) == 1 and '=' in parts[0]:
            key, value = parts[0].split('=', 1)
            env[key] = value
    origin = urlparse(env.get('CATALOG_PUBLIC_ORIGIN', ''))
    if (origin.scheme != 'https' or origin.netloc != ip or origin.path not in ('', '/')
            or origin.query or origin.fragment or env.get('CATALOG_AUTH_ENABLED') != '1'
            or env.get('CATALOG_COOKIE_SECURE') != '1'):
        parser.error('应用须启用团队登录及 Secure Cookie，并配置相同 IP 的 HTTPS 地址')
    certificate = '/etc/letsencrypt/live/jiwu-ip/fullchain.pem'
    run('openssl', 'x509', '-in', certificate, '-noout', '-checkip', ip)
    run('openssl', 'x509', '-in', certificate, '-noout', '-checkend', '0')
    run(nginx, '-t')
    files = configuration(ip, nginx)
    # Check every destination before any write. Do not replace manually managed files.
    for path, (content, _) in files.items():
        if path.is_symlink() or (path.exists() and path.read_text() != content):
            parser.error(f'已有不同配置，未覆盖：{path}')
    created = []
    try:
        for path, (content, mode) in files.items():
            if not path.exists():
                # Link a complete temporary file into place without overwrite races.
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
                    temporary = Path(file.name)
                    os.fchmod(file.fileno(), mode)
                    file.write(content.encode())
                    file.flush()
                    os.fsync(file.fileno())
                try:
                    os.link(temporary, path)
                    created.append(path)
                finally:
                    temporary.unlink()
        run(nginx, '-t')
        run('systemd-analyze', 'verify', '/etc/systemd/system/jiwu-cert-renew.service',
            '/etc/systemd/system/jiwu-cert-renew.timer')
    except Exception:
        for path in reversed(created):
            path.unlink()
        raise
    run(nginx, '-s', 'reload')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', 'jiwu-cert-renew.timer')
    # Real TLS verification, including IP SAN and trust chain. No -k bypass.
    result = subprocess.run(['curl', '--fail', '--silent', '--show-error', '--retry', '5',
        '--retry-connrefused', '--retry-delay', '1', '--max-time', '10', '--noproxy', '*',
        '--resolve', f'{ip}:443:127.0.0.1', f'https://{ip}/api/health'],
        check=True, capture_output=True, text=True)
    health = json.loads(result.stdout)
    if health.get('status') != 'ok' or health.get('database') != 'postgresql' or health.get('images') != 'filesystem':
        raise RuntimeError('HTTPS 返回的后端状态与当前部署不符')
    print(json.dumps(health, ensure_ascii=False), flush=True)
    run('systemctl', 'list-timers', 'jiwu-cert-renew.timer', '--no-pager')
    print('正在测试证书续期，可能需要几分钟……', flush=True)
    run(certbot, 'renew', '--cert-name', 'jiwu-ip', '--dry-run', '--run-deploy-hooks',
        '--deploy-hook', '/usr/local/sbin/jiwu-cert-reload')
    print(f'本机 HTTPS 与模拟续期通过。请浏览器访问 https://{ip} 验证公网和登录。')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as exc:
        if exc.stderr:
            print(exc.stderr)
        raise SystemExit('部署检查未通过，请保留输出排查；未宣称部署完成。') from None
