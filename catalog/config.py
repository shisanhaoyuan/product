"""Read a private systemd-compatible environment file without executing shell code."""
import os
import shlex
from pathlib import Path


def load_env_file(path):
    path = Path(path)
    if path.stat().st_mode & 0o077:
        raise ValueError('环境文件需设为 chmod 600，避免泄露数据库和 OSS 凭据')
    for line in path.read_text().splitlines():
        parts = shlex.split(line, comments=True)
        if not parts:
            continue
        if len(parts) != 1 or '=' not in parts[0]:
            raise ValueError('环境文件需使用 KEY=value 格式，带空格的值请用引号')
        key, value = parts[0].split('=',1)
        if not key.startswith(('CATALOG_', 'OSS_')):
            raise ValueError('环境文件包含未支持的配置项')
        os.environ[key] = value
