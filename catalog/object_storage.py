"""Independent, content-addressed image storage with integrity verification."""
import hashlib
import os
import re
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse



MAX_IMAGE = 8 * 1024 * 1024


class StorageError(RuntimeError):
    pass


class OSSStorage:
    backend = 'oss'
    def __init__(self, *, client=None):
        import alibabacloud_oss_v2 as oss
        self.sdk = oss
        self.bucket = os.environ.get('CATALOG_OSS_BUCKET', '')
        self.region = os.environ.get('CATALOG_OSS_REGION', '')
        self.endpoint = os.environ.get('CATALOG_OSS_ENDPOINT', '').rstrip('/')
        self.prefix = os.environ.get('CATALOG_OSS_PREFIX', 'jiwu/images').strip('/')
        endpoint = urlparse(self.endpoint)
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', self.bucket):
            raise StorageError('请配置有效的 CATALOG_OSS_BUCKET')
        if (not re.fullmatch(r'[a-z0-9-]+', self.region) or endpoint.scheme != 'https'
                or endpoint.hostname not in {f'oss-{self.region}.aliyuncs.com', f'oss-{self.region}-internal.aliyuncs.com'}
                or endpoint.path or endpoint.query or endpoint.fragment or endpoint.username or endpoint.password or endpoint.port):
            raise StorageError('OSS endpoint 必须是该区域的官方 HTTPS 地址')
        if not re.fullmatch(r'[a-zA-Z0-9/_-]{1,200}', self.prefix):
            raise StorageError('OSS 图片前缀无效')
        if client is None:
            if not os.environ.get('OSS_ACCESS_KEY_ID') or not os.environ.get('OSS_ACCESS_KEY_SECRET'):
                raise StorageError('请在服务器配置 OSS 凭据，不要将密钥写入代码或聊天')
            config = oss.Config(region=self.region, endpoint=self.endpoint,
                credentials_provider=oss.credentials.EnvironmentVariableCredentialsProvider(),
                connect_timeout=8, readwrite_timeout=30, retry_max_attempts=3,
                enabled_redirect=False)
            client = oss.Client(config)
        self.client = client
        # Internal and public endpoints address the same regional bucket.
        self.endpoint = f'https://oss-{self.region}.aliyuncs.com'

    def put(self, digest, content, mime):
        if not isinstance(content, (bytes, bytearray, memoryview)) or hashlib.sha256(content).hexdigest() != digest or len(content) > MAX_IMAGE:
            raise StorageError('图片摘要或大小异常')
        oss = self.sdk
        key = f'{self.prefix}/{digest[:2]}/{digest}'
        try:
            try:
                self.client.put_object(oss.PutObjectRequest(bucket=self.bucket, key=key, body=content,
                    content_type=mime, content_length=len(content), forbid_overwrite=True,
                    object_acl='private', metadata={'sha256': digest}))
            except oss.exceptions.ServiceError as exc:
                if exc.code not in ('FileAlreadyExists', 'ObjectAlreadyExists'):
                    raise
            # Verify bytes, not just an ETag (multipart/encrypted ETags aren't SHA-256).
            row = dict(backend='oss', digest=digest, object_key=key, size=len(content), mime=mime,
                       bucket=self.bucket, endpoint=self.endpoint)
            self.get(row)
            return row
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError('OSS 图片上传或校验失败，商品图片未更改') from exc

    def get(self, row):
        row = dict(row)
        oss = self.sdk
        if row.get('backend','oss') != 'oss' or row['bucket'] != self.bucket or row['endpoint'] != self.endpoint:
            raise StorageError('图片存储位置与当前 OSS 配置不符，请先迁移存储配置')
        try:
            result = self.client.get_object(oss.GetObjectRequest(bucket=self.bucket, key=row['object_key']))
            try:
                content = result.body.read(MAX_IMAGE + 1)
            finally:
                result.body.close()
            if len(content) != row['size'] or hashlib.sha256(content).hexdigest() != row['digest']:
                raise StorageError('OSS 图片完整性校验失败')
            return content
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError('OSS 图片暂时无法读取，请检查存储服务') from exc


class FileStorage:
    """Content-addressed files, atomically created outside the source/web tree."""
    backend = 'filesystem'

    def __init__(self, root):
        if not root or not Path(root).is_absolute():
            raise StorageError('CATALOG_IMAGE_ROOT 必须是独立的绝对路径')
        self.root = Path(root).resolve()
        if self.root == Path('/') or self.root.is_relative_to(Path(__file__).resolve().parents[1]):
            raise StorageError('图片目录不能位于项目、静态网站目录或文件系统根目录')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def path_for(self, key):
        if not re.fullmatch(r'[a-f0-9]{2}/[a-f0-9]{64}', key):
            raise StorageError('图片对象路径无效')
        path = self.root / key
        if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise StorageError('图片路径不能包含符号链接或越过存储目录')
        return path

    def put(self, digest, content, mime):
        if not isinstance(content, (bytes, bytearray, memoryview)) or hashlib.sha256(content).hexdigest() != digest or len(content) > MAX_IMAGE:
            raise StorageError('图片摘要或大小异常')
        row = dict(backend=self.backend, digest=digest,object_key=f'{digest[:2]}/{digest}',
                   size=len(content),mime=mime,bucket='',endpoint='')
        path = self.path_for(row['object_key'])
        temp = None
        try:
            path.parent.mkdir(mode=0o700,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as out:
                temp = Path(out.name)
                out.write(content)
                out.flush()
                os.fsync(out.fileno())
            try:
                os.link(temp, path)  # Never truncate an existing object, including races.
            except FileExistsError:
                pass
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self.get(row)
            return row
        except StorageError:
            raise
        except OSError:
            raise StorageError('图片文件写入失败，请检查磁盘空间和目录权限') from None
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def get(self, row):
        row = dict(row)
        if row.get('backend') != self.backend:
            raise StorageError('图片所属存储后端与当前配置不一致')
        path = self.path_for(row['object_key'])
        try:
            with path.open('rb') as source:
                content = source.read(MAX_IMAGE + 1)
        except OSError:
            raise StorageError('图片文件不存在或无法读取，请检查目录及备份') from None
        if len(content) != row['size'] or hashlib.sha256(content).hexdigest() != row['digest']:
            raise StorageError('图片文件完整性校验失败')
        return content


def configured_storage(catalog):
    backend = os.environ.get('CATALOG_IMAGE_STORAGE', 'sqlite' if not catalog.postgres else 'filesystem')
    if backend == 'oss':
        return OSSStorage()
    if backend == 'filesystem':
        return FileStorage(os.environ.get('CATALOG_IMAGE_ROOT', ''))
    if backend != 'sqlite' or catalog.postgres:
        raise StorageError('PostgreSQL 部署必须配置 filesystem 或 oss 图片存储')
    return None


def save_metadata(db, row, actor_id=None, created_at=None):
    db.execute('''INSERT INTO image_objects(digest,object_key,mime,size,created_at,actor_id,bucket,endpoint,backend)
        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(digest) DO NOTHING''',
        (row['digest'],row['object_key'],row['mime'],row['size'],
         created_at or datetime.now(timezone.utc).isoformat(),actor_id,row['bucket'],row['endpoint'],row['backend']))
    persisted = db.execute('SELECT * FROM image_objects WHERE digest=?', (row['digest'],)).fetchone()
    if any(persisted[k] != row[k] for k in ('object_key','mime','size','bucket','endpoint','backend')):
        raise StorageError('已有图片的存储位置冲突，未覆盖原记录')
