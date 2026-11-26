"""Isolated Canada store settings UI; mocked Walmart, no live credentials or writes."""
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

import httpx
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from catalog.app import create_app
from catalog.credential_vault import initialize_key
from catalog.walmart_publishing_client import WalmartPublishingClient
from test_walmart_accounts import Remote, INPUT
from test_walmart_canada_publishing import CanadaRemote

with tempfile.TemporaryDirectory(prefix='ca-preparation-') as folder:
    root=Path(folder)
    os.environ.update(CATALOG_AUTH_ENABLED='0',CATALOG_LISTING_AI_ENABLED='0',CATALOG_LISTING_RESULT_WORKER='0',CATALOG_CREDENTIAL_KEY_FILE=str(root/'vault.key'))
    initialize_key(root/'vault.key')
    app=create_app(root/'test.db');client=TestClient(app)
    sid=next(s['id'] for s in client.get('/api/listing-stores').json() if s['name']=='MYZ-L-12')
    Remote().install(app)
    draft=client.post('/api/walmart/accounts/preview',json=dict(store_id=sid,marketplace='CA',**INPUT)).json()
    assert client.post(f'/api/walmart/accounts/{sid}/bind',json=dict(verification_id=draft['verification_id'])).status_code==200
    remote=CanadaRemote()
    app.state.walmart_publisher=WalmartPublishingClient(httpx.MockTransport(remote.handler))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(origin+'/#/walmart-accounts')
            page.get_by_role('searchbox',name='搜索绑定店铺').fill('MYZ-L-12')
            page.get_by_role('button',name='生成前提配置',exact=True).click()
            dialog=page.get_by_role('dialog')
            expect(dialog.get_by_role('heading',name='MYZ-L-12 · 生成前提配置')).to_be_visible()
            expect(dialog.get_by_role('status')).to_have_count(0,timeout=10000)
            expect(dialog.get_by_role('button',name='添加豁免')).to_have_count(0)
            expect(dialog).to_contain_text('加拿大站当前使用默认库存')
            dialog.get_by_label('店铺默认品牌').fill('Myxchome')
            dialog.get_by_role('button',name='核验并保存配置').click()
            expect(dialog.get_by_role('status')).to_contain_text('已核验并保存',timeout=10000)
            page.screenshot(path=str(ROOT/'validation/canada-preparation-desktop.png'))
            dialog.get_by_role('button',name='关闭配置').click()
            expect(page.locator('.wm-store')).to_contain_text('默认品牌：Myxchome')
            page.get_by_role('button',name='生成前提配置',exact=True).click()
            expect(dialog.get_by_label('店铺默认品牌')).to_have_value('Myxchome')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(ROOT/'validation/canada-preparation-mobile.png'))
            assert not errors,errors
            assert not remote.posts
            browser.close()
            print('PASS: Canada settings entry, brand save/reopen/card refresh, no US exemptions, desktop/mobile; no publishing')
    finally:
        server.should_exit=True
        thread.join(timeout=5)
