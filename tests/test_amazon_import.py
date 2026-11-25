import io
import json
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.amazon_import import parse_inputs, run_fallback
from catalog.amazon_import_sheet import parse_sheet
from test_auth import team, login
from test_source_capture import page, offer

BASE='/api/integrations/amazon-collector'
A='B000000000'
B='B000000001'


@pytest.fixture
def local_import(tmp_path,monkeypatch):
    monkeypatch.delenv('CATALOG_AUTH_ENABLED',raising=False)
    monkeypatch.setattr('catalog.source_images.collect_amazon_images',lambda *a:dict(state='complete',images=[],saved=[],message='无图片'))
    app=create_app(tmp_path/'collector.db')
    return TestClient(app),app.state.catalog


def new(client,text=A):
    result=client.post(BASE+'/jobs',json=dict(text=text,include_variants=True,request_id=str(uuid.uuid4())))
    assert result.status_code==200,result.text
    return result.json()['id']


def action(client,jid,name,body=None):
    result=client.post(f'{BASE}/jobs/{jid}/{name}',json=body)
    assert result.status_code==200,result.text
    return result.json()


def html(asin=A,children=None,price='37.99',color='Brown'):
    return page(asin,offer(price=price)) + '<h1 id="productTitle">Bed '+asin+'</h1>' + '<div id="jiwu-attributes">'+json.dumps({'color name':color})+'</div>'+ '<div id="jiwu-variants">'+json.dumps(children or [])+'</div>'


def save(client,jid,task,children=None,price='37.99'):
    return action(client,jid,'page',dict(**task,source_url='https://www.amazon.com/dp/'+task['asin'],html=html(task['asin'],children,price)))


def test_input_domains_and_dedup():
    assert parse_inputs(A+'\nhttps://www.amazon.com/title/dp/'+A+'?th=1\nhttps://amazon.com/gp/product/'+B)==[A,B]
    for url in ['https://evil.test/dp/'+A,'https://www.amazon.com@evil.test/dp/'+A,'file:///dp/'+A,'https://www.amazon.ca/dp/'+A]:
        with pytest.raises(HTTPException):parse_inputs(url)


def test_variants_atomic_import_idempotence_and_independent_prices(local_import):
    client,catalog=local_import
    jid=new(client,A+'\n'+B)
    task=action(client,jid,'claim')['task']
    job=save(client,jid,task,[A,B])
    assert len(job['items'])==2  # child input rehomed into this family
    child=action(client,jid,'claim')['task']
    assert child['root_asin']==A and child['asin']==B
    save(client,jid,child,price='41.99')
    assert action(client,jid,'claim')['task'] is None
    result=action(client,jid,'finish')['result']
    assert result['created']==1
    stored=catalog.get(result['listing_ids'][0])
    assert {v['asin']:v['price'] for v in stored['variants']}=={A:'37.99',B:'41.99'}
    assert stored['variants'][0]['attributes']['color name']=='Brown'
    assert stored['stock'] is None and not stored['fill_complete']
    assert action(client,jid,'finish')['result']==result
    with catalog.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM source_captures').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM listings').fetchone()[0]==1
    next_job=new(client,B)
    assert action(client,next_job,'claim')['task']['asin']==A


def test_cancelled_token_and_wrong_asin_never_save(local_import):
    client,_=local_import;jid=new(client);task=action(client,jid,'claim')['task']
    wrong=dict(**task,source_url='https://www.amazon.com/dp/'+B,html=html(B))
    assert client.post(f'{BASE}/jobs/{jid}/page',json=wrong).status_code==422
    action(client,jid,'pause')
    assert client.post(f'{BASE}/jobs/{jid}/page',json=dict(**task,source_url='https://www.amazon.com/dp/'+A,html=html())).status_code==409
    assert action(client,jid,'claim')['task']['token']!=task['token']


def test_stale_capture_does_not_overwrite_newer_job(local_import):
    client,catalog=local_import
    old=new(client);save(client,old,action(client,old,'claim')['task'],price='30.00')
    newer=new(client);save(client,newer,action(client,newer,'claim')['task'],price='40.00')
    result=action(client,newer,'finish')['result'];lid=result['listing_ids'][0]
    stale=action(client,old,'finish')['result']
    assert stale['stale_roots']==[A] and not stale['listing_ids']
    assert catalog.get(lid)['price']=='40.00'
    with catalog.connect() as db:assert db.execute('SELECT COUNT(*) FROM source_captures').fetchone()[0]==1


def test_import_preserves_manual_edits_and_missing_child(local_import):
    client,catalog=local_import
    jid=new(client);save(client,jid,action(client,jid,'claim')['task'],[A,B]);save(client,jid,action(client,jid,'claim')['task'])
    lid=action(client,jid,'finish')['result']['listing_ids'][0]
    current=catalog.get(lid)
    response=client.patch(f'/api/products/{lid}/edits',json=dict(expected_revision=current['local_edit']['revision'],field='copy.title',value='Human title'))
    assert response.status_code==200,response.text
    jid=new(client);save(client,jid,action(client,jid,'claim')['task'],[A,B],price='40.00')
    action(client,jid,'finish')
    current=catalog.get(lid)
    assert current['copy']['title']=='Human title' and len(current['variants'])==2
    assert current['variants'][1]['price']=='37.99'


def test_transaction_rolls_back_if_capture_save_fails(local_import,monkeypatch):
    client,catalog=local_import;jid=new(client);save(client,jid,action(client,jid,'claim')['task'])
    def fail(*a,**k):raise RuntimeError('simulated save failure')
    monkeypatch.setattr('catalog.amazon_import.save_capture',fail)
    with pytest.raises(RuntimeError):action(client,jid,'finish')
    with catalog.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM listings').fetchone()[0]==0
        assert db.execute('SELECT state FROM amazon_import_jobs WHERE id=?',(jid,)).fetchone()[0]=='collecting'


def test_server_fallback_race_with_success_and_pause(local_import,monkeypatch):
    client,catalog=local_import
    monkeypatch.setattr('catalog.amazon_browser.configured',lambda:True)
    jid=new(client);task=action(client,jid,'claim')['task']
    action(client,jid,'fallback',task)
    monkeypatch.setattr('catalog.amazon_browser.fetch_page',lambda asin:html(asin))
    assert run_fallback(catalog)
    assert save(client,jid,task)['items'][0]['state']=='done'
    assert not run_fallback(catalog)
    second=new(client);task=action(client,second,'claim')['task'];action(client,second,'fallback',task)
    def during(asin):
        action(client,second,'pause')
        return html(asin)
    monkeypatch.setattr('catalog.amazon_browser.fetch_page',during)
    assert run_fallback(catalog)
    job=client.get(f'{BASE}/jobs/{second}').json()
    assert job['state']=='paused' and job['items'][0]['state']=='pending'


def test_owner_viewer_and_csrf(team):
    editor,_=login(team,'editor');admin,_=login(team);viewer,_=login(team,'viewer')
    jid=new(editor)
    assert admin.get(f'{BASE}/jobs/{jid}').status_code==404
    assert admin.post(f'{BASE}/jobs/{jid}/claim').status_code==404
    assert viewer.post(BASE+'/jobs',json=dict(text=A,include_variants=True,request_id=str(uuid.uuid4()))).status_code==403
    assert editor.post(f'{BASE}/jobs/{jid}/claim',headers={'X-CSRF-Token':''}).status_code==403


def test_csv_and_excel_no_formula_execution():
    data=parse_sheet(('商品链接,备注\nhttps://www.amazon.com/dp/'+A+',a\n'+A+',duplicate\nhttps://evil.test/,bad\n').encode(),'links.csv')
    assert data['asins']==[A] and data['duplicate']==1 and data['invalid']==1
    from openpyxl import Workbook
    wb=Workbook();ws=wb.active;ws.append(['ASIN']);ws.append([A]);ws.append(['=HYPERLINK("https://example.com")']);ws.append([B]);buf=io.BytesIO();wb.save(buf)
    data=parse_sheet(buf.getvalue(),'links.xlsx')
    assert data['asins']==[A,B] and data['invalid']==1
    with pytest.raises(HTTPException):parse_sheet(buf.getvalue(),'links.xls')


def test_wrong_zip_and_identity_not_imported(local_import):
    client,_=local_import;jid=new(client);task=action(client,jid,'claim')['task']
    for value in [html().replace('10001','90001'), html(B), html().replace('id="productTitle"','id="otherTitle"')]:
        response=client.post(f'{BASE}/jobs/{jid}/page',json=dict(**task,source_url='https://www.amazon.com/dp/'+A,html=value))
        assert response.status_code==422,response.text


def test_child_discovers_extra_combination_and_rejects_wrong_family(local_import):
    client,_=local_import;jid=new(client)
    task=action(client,jid,'claim')['task']
    markup=html(A,[A,B])+'<div id="jiwu-variant-evidence">'+json.dumps(dict(current_asin=A,parent_asins=['B111111111']))+'</div>'
    action(client,jid,'page',dict(**task,source_url='https://www.amazon.com/dp/'+A,html=markup))
    task=action(client,jid,'claim')['task']
    wrong=html(B)+'<div id="jiwu-variant-evidence">'+json.dumps(dict(current_asin=B,parent_asins=['B222222222']))+'</div>'
    response=client.post(f'{BASE}/jobs/{jid}/page',json=dict(**task,source_url='https://www.amazon.com/dp/'+B,html=wrong))
    assert response.status_code==422
    job=save(client,jid,task,[A,B,'B000000002'])
    assert len(job['items'])==3


def test_create_request_id_and_single_active_browser(local_import):
    client,_=local_import
    body=dict(text=A,include_variants=True,request_id=str(uuid.uuid4()))
    first=client.post(BASE+'/jobs',json=body).json()
    assert client.post(BASE+'/jobs',json=body).json()==first
    assert client.post(BASE+'/jobs',json=body|{'text':B}).status_code==409
    action(client,first['id'],'claim')
    second=new(client,B)
    assert client.post(f'{BASE}/jobs/{second}/claim').status_code==409


def test_large_spreadsheet_upload_over_old_limit_and_150mb_boundary(local_import,tmp_path):
    import zipfile
    from openpyxl import Workbook
    from catalog.amazon_import_sheet import MAX_FILE
    wb=Workbook();wb.active.append(['ASIN']);wb.active.append([A]);buf=io.BytesIO();wb.save(buf)
    # A normal workbook with a large non-worksheet attachment must pass the old 8 MB boundary.
    with zipfile.ZipFile(buf,'a',compression=zipfile.ZIP_STORED) as archive:
        archive.writestr('attachment.bin',b'0'*(21*1024*1024))
    client,_=local_import
    response=client.post(BASE+'/spreadsheet?filename=large.xlsx',content=buf.getvalue(),headers={'Content-Type':'application/octet-stream'})
    assert response.status_code==200,response.text
    assert response.json()['asins']==[A]
    assert MAX_FILE==150*1024*1024
    assert client.post(BASE+'/spreadsheet?filename=large.xlsx',content=b'x',headers={'Content-Length':str(MAX_FILE+1)}).status_code==413
    assert client.post('/api/imports',content=b'x',headers={'Content-Length':str(21*1024*1024)}).status_code==413
    # Sparse file checks the upper boundary without allocating 150 MB in memory.
    with (tmp_path/'oversize.xlsx').open('w+b') as stream:
        stream.truncate(MAX_FILE+1)
        with pytest.raises(HTTPException,match='150MB'):
            parse_sheet(stream,'oversize.xlsx')


def test_partial_image_cache_preserves_remaining_gallery(local_import,monkeypatch):
    client,catalog=local_import
    from catalog.amazon_import import verified_page
    first='https://m.media-amazon.com/images/I/first.jpg';second='https://m.media-amazon.com/images/I/second.jpg'
    cached='/api/products/images/assets/'+'a'*64
    monkeypatch.setattr('catalog.amazon_import.amazon_images',lambda *a:[first,second])
    monkeypatch.setattr('catalog.source_images.collect_amazon_images',lambda *a:dict(state='partial',images=[cached],saved=[{'source_url':first,'url':cached}],message='一张图片下载失败'))
    data=verified_page(catalog,A,A,html(),'https://www.amazon.com/dp/'+A,'browser')
    assert data['images']==[cached,second]
    assert '一张图片下载失败' in data['warnings']
