import argparse
import json
import os
from pathlib import Path
from .db import Catalog
from .models import ImportBundle


def main():
    root=Path(__file__).resolve().parent.parent
    parser=argparse.ArgumentParser(description='独立商品库：运行、导入与备份')
    parser.add_argument('--db',default=os.environ.get('CATALOG_DB',str(root/'data/catalog.db')))
    sub=parser.add_subparsers(dest='command',required=True)
    serve=sub.add_parser('serve');serve.add_argument('--host',default='127.0.0.1');serve.add_argument('--port',type=int,default=8765)
    imp=sub.add_parser('import-digitemperor');imp.add_argument('--logs',type=Path,required=True)
    imp=sub.add_parser('import-json');imp.add_argument('file',type=Path)
    backup=sub.add_parser('backup');backup.add_argument('file',type=Path)
    admin=sub.add_parser('create-admin',help='交互创建管理员（密码不回显）');admin.add_argument('username')
    reset=sub.add_parser('reset-password',help='服务器恢复账号密码并撤销所有会话');reset.add_argument('username')
    args=parser.parse_args()
    if args.command=='serve':
        if args.host not in {'127.0.0.1','localhost','::1'} and os.environ.get('CATALOG_AUTH_ENABLED') != '1':
            parser.error('监听非本机地址前必须启用 CATALOG_AUTH_ENABLED=1；宝塔反向代理请保持 127.0.0.1')
        os.environ['CATALOG_DB']=args.db
        import uvicorn
        uvicorn.run('catalog.app:app',host=args.host,port=args.port)
    elif args.command=='import-digitemperor':
        from .adapters.digitemperor import build_bundle
        bundle,info=build_bundle(args.logs)
        info['result']=Catalog(args.db).ingest(bundle)
        diag=Path(args.db).parent/'import-diagnostics.json'
        diag.write_text(json.dumps(info,ensure_ascii=False,indent=2))
        print(json.dumps(info,ensure_ascii=False,indent=2))
    elif args.command=='import-json':
        result=Catalog(args.db).ingest(ImportBundle.model_validate_json(args.file.read_text()))
        print(json.dumps(result,ensure_ascii=False))
    elif args.command in {'create-admin','reset-password'}:
        from getpass import getpass
        from .auth import AuthStore
        password=getpass('新密码（至少 12 位）：')
        if password!=getpass('再次输入密码：'):parser.error('两次密码不一致')
        try:
            auth=AuthStore(Catalog(args.db))
            if args.command=='create-admin':
                auth.create_user(args.username,password,'admin',temporary=False)
            else:
                from .auth import password_hash
                hashed=password_hash(password)
                with auth.catalog.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row=db.execute('SELECT id FROM users WHERE username=?',(args.username.lower(),)).fetchone()
                    if not row:parser.error('账号不存在')
                    db.execute('UPDATE users SET password_hash=?,must_change_password=1 WHERE id=?',(hashed,row['id']))
                    db.execute('DELETE FROM sessions WHERE user_id=?',(row['id'],))
                    auth.audit(db,None,'password.reset',row['id'])
        except ValueError as exc:parser.error(str(exc))
        print('管理员已创建' if args.command=='create-admin' else '密码已重置，下次登录需修改密码')
    elif args.command=='backup':
        import sqlite3
        target=args.file.resolve()
        if target.exists():parser.error('备份目标已经存在，请选择新文件名')
        target.parent.mkdir(parents=True,exist_ok=True)
        with Catalog(args.db).connect() as source,sqlite3.connect(target) as destination:source.backup(destination)
        print(target)


if __name__=='__main__':main()
