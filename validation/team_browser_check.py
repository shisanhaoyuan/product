"""Exercise team UI against a disposable database and server; no production writes."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from catalog.auth import AuthStore
from catalog.db import Catalog


def main():
    with tempfile.TemporaryDirectory(prefix='jiwu-team-check-') as directory:
        database = Path(directory) / 'test.db'
        catalog = Catalog(database)
        auth = AuthStore(catalog)
        password = secrets.token_urlsafe(24)
        auth.create_user('admin', password, 'admin', temporary=False)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        env = os.environ | {'CATALOG_DB': str(database), 'CATALOG_AUTH_ENABLED': '1',
                            'CATALOG_PUBLIC_ORIGIN': origin, 'CATALOG_COOKIE_SECURE': '0'}
        log = open(Path(directory) / 'server.log', 'w')
        process = subprocess.Popen([sys.executable, '-m', 'catalog', 'serve', '--port', str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)
        try:
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError('Test server exited')
                try:
                    urllib.request.urlopen(origin + '/api/health', timeout=1)
                    break
                except OSError:
                    time.sleep(.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, channel='chrome')
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(origin)
                expect(page.get_by_role('heading', name='登录商品库')).to_be_visible()
                page.screenshot(path=str(ROOT / 'validation/login.png'))
                page.get_by_label('账号', exact=True).fill('admin')
                page.get_by_label('密码', exact=True).fill(password)
                page.get_by_role('button', name='登录', exact=True).click()
                expect(page.get_by_role('heading', name='商品库 0')).to_be_visible()
                page.get_by_role('link', name='团队', exact=True).click()
                expect(page.get_by_role('heading', name='团队账号')).to_be_visible()
                temporary = secrets.token_urlsafe(24)
                page.get_by_label('账号', exact=True).fill('member')
                page.get_by_label('临时密码').fill(temporary)
                page.get_by_role('button', name='添加成员').click()
                expect(page.locator('.integration-grid').get_by_role('cell', name='member', exact=True)).to_be_visible()
                page.screenshot(path=str(ROOT / 'validation/team.png'))
                for width in [390, 801, 1024]:
                    page.set_viewport_size({'width': width, 'height': 900})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.get_by_role('button', name='退出', exact=True).click()
                expect(page.get_by_role('heading', name='登录商品库')).to_be_visible()
                page.get_by_label('账号', exact=True).fill('member')
                page.get_by_label('密码', exact=True).fill(temporary)
                page.get_by_role('button', name='登录', exact=True).click()
                expect(page.get_by_role('heading', name='账号与密码')).to_be_visible()
                changed = secrets.token_urlsafe(24)
                page.get_by_label('当前密码').fill(temporary)
                page.get_by_label('新密码（至少 12 位）', exact=True).fill(changed)
                page.get_by_label('确认新密码').fill(changed)
                page.get_by_role('button', name='修改并重新登录').click()
                expect(page.get_by_role('heading', name='登录商品库')).to_be_visible()
                page.get_by_label('账号', exact=True).fill('member')
                page.get_by_label('密码', exact=True).fill(changed)
                page.get_by_role('button', name='登录', exact=True).click()
                expect(page.get_by_role('heading', name='商品库 0')).to_be_visible()
                page.get_by_role('link', name='数据接入', exact=True).click()
                expect(page.get_by_text('当前账号为只读成员，导入需由编辑员或管理员操作。')).to_be_visible()
                expect(page.locator('#import-form')).to_have_count(0)
                page.goto(origin + '/#/users')
                expect(page.get_by_role('heading', name='商品库 0')).to_be_visible()
                # Disable in the disposable DB; next API request must clear the UI session.
                auth.update_user(2, 'viewer', False, 1)
                page.get_by_role('link', name='导入记录', exact=True).click()
                expect(page.get_by_role('heading', name='登录商品库')).to_be_visible()
                assert not errors, errors
                browser.close()
            print('Team UI passed: login, member creation, initial password change, viewer restrictions, session revocation, responsive layout.')
        finally:
            process.terminate()
            process.wait(timeout=10)
            log.close()


if __name__ == '__main__':
    main()
