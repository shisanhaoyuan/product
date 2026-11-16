#!/usr/bin/env bash
# One-time master-key setup; individual shop credentials are entered only in the UI.
set -Eeuo pipefail
[[ $EUID == 0 && $# == 0 ]] || { echo '请以 root 运行，不带参数。' >&2; exit 1; }
cd /www/wwwroot/jiwu
.venv/bin/python - <<'PY'
import os
from pathlib import Path
from catalog.config import load_env_file
from catalog.credential_vault import initialize_key, read_key
from catalog.db import Catalog
load_env_file('/etc/jiwu.env')
env = Path('/etc/jiwu.env')
original = env.read_text()
configured = os.environ.get('CATALOG_CREDENTIAL_KEY_FILE')
path = Path(configured or '/www/server/jiwu-data/walmart-credentials.key')
if path != Path('/www/server/jiwu-data/walmart-credentials.key'):
    raise SystemExit('检测到自定义密钥路径，未修改；请保留原路径并检查服务读写权限。')
catalog = Catalog(os.environ['CATALOG_DATABASE_URL'])
with catalog.connect() as db:
    count = db.execute("SELECT COUNT(*) FROM walmart_accounts WHERE encrypted_credentials<>''").fetchone()[0]
    drafts = db.execute('SELECT COUNT(*) FROM walmart_binding_previews').fetchone()[0]
if not path.exists():
    if count or drafts:
        raise SystemExit('数据库已有加密凭证，必须恢复原主密钥，不能生成新密钥。')
    initialize_key(path)
import pwd
user = pwd.getpwnam('jiwu')
# Refuse symlinks and unsafe files before changing ownership.
os.environ['CATALOG_CREDENTIAL_KEY_FILE'] = str(path)
read_key()
os.chown(path, user.pw_uid, user.pw_gid)
if not configured:
    if 'CATALOG_CREDENTIAL_KEY_FILE=' in original:
        raise SystemExit('配置包含空值或重复项，请检查 CATALOG_CREDENTIAL_KEY_FILE，未覆盖。')
    with env.open('a') as file:
        file.write('\nCATALOG_CREDENTIAL_KEY_FILE=' + str(path) + '\n')
        file.flush()
        os.fsync(file.fileno())
print('加密主密钥已就绪；未输出或更换已有密钥。请将该文件单独安全备份。')
PY
runuser -u jiwu -- env CATALOG_CREDENTIAL_KEY_FILE=/www/server/jiwu-data/walmart-credentials.key .venv/bin/python - <<'PY'
from catalog.credential_vault import read_key
read_key()
print('服务账号读取权限检查通过。')
PY
systemctl restart jiwu
systemctl is-active jiwu
