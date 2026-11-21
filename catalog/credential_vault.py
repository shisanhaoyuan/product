"""Authenticated encryption for store credentials; the master key never lives in SQL."""
import argparse
import base64
import json
import os
import stat
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class VaultError(Exception):
    pass


def key_path():
    return Path(os.environ.get('CATALOG_CREDENTIAL_KEY_FILE') or
                Path(__file__).resolve().parent.parent / 'data/walmart-credentials.key')


def read_key():
    try:
        # Refuse symlinks and broadly readable files, including on restore.
        fd = os.open(key_path(), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or (os.name != 'nt' and info.st_mode & 0o077):
                raise ValueError('permissions')
            raw = file.read(128)
        key = base64.b64decode(raw.strip(), altchars=b'-_', validate=True)
        if len(key) != 32:
            raise ValueError('length')
        return key
    except (OSError, ValueError):
        raise VaultError('凭证加密主密钥不可用，请管理员完成一次性初始化或检查文件权限') from None


def encrypt(store_id, payload, marketplace='US'):
    if marketplace not in ('US', 'CA'):
        raise VaultError('不支持的沃尔玛站点')
    nonce = os.urandom(12)
    encrypted = AESGCM(read_key()).encrypt(nonce, json.dumps(payload).encode(), f'walmart:{marketplace}:{store_id}:v1'.encode())
    return 'v1.' + base64.urlsafe_b64encode(nonce + encrypted).decode()


def decrypt(store_id, ciphertext, marketplace='US'):
    key = read_key()
    try:
        if marketplace not in ('US', 'CA') or not ciphertext.startswith('v1.'):
            raise ValueError('version')
        raw = base64.b64decode(ciphertext[3:], altchars=b'-_', validate=True)
        result = json.loads(AESGCM(key).decrypt(raw[:12], raw[12:], f'walmart:{marketplace}:{store_id}:v1'.encode()))
        if not isinstance(result, dict):
            raise ValueError('payload')
        return result
    except Exception:
        raise VaultError('凭证无法解密；请检查是否恢复了原加密主密钥，不要覆盖原密钥') from None


def initialize_key(path):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Never replace an existing key, even an invalid one.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as file:
        file.write(base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)) + b'\n')
        file.flush()
        os.fsync(file.fileno())


def main():
    parser = argparse.ArgumentParser(description='一次性创建店铺凭证加密主密钥；不输出密钥，不覆盖已有文件')
    parser.add_argument('action', choices=['init'])
    parser.add_argument('path', nargs='?', type=Path, default=key_path())
    args = parser.parse_args()
    try:
        initialize_key(args.path)
    except FileExistsError:
        parser.exit(1, '文件已存在，未覆盖；请保留原密钥。\n')
    print('加密主密钥已创建。请单独安全备份，不要提交 Git；更换部署位置时需恢复原密钥。')


if __name__ == '__main__':
    main()
