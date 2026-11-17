"""Spreadsheet imports and random allocation use isolated data, never live publishing."""
import copy
import io
import json
from concurrent.futures import ThreadPoolExecutor
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from fastapi import HTTPException

from catalog.identifier_spreadsheets import parse_spreadsheet, code_text
from catalog.listing_prerequisites import pool_assignment
from catalog.database import write_lock
from catalog.walmart_publishing import default_rows
from test_prelisting import local, inputs, create
from test_auth import team, login
from test_walmart_publishing import publishing, ready, send, warehouse, settings, vault


def ean(index):
    body='937042'+str(index).zfill(6)
    return body+str((-sum(int(v)*(1 if i%2==0 else 3) for i,v in enumerate(body)))%10)


def xlsx(rows):
    """Small OOXML test fixtures, with exact types and optional zero mask."""
    out=io.BytesIO()
    with ZipFile(out,'w',ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="编码表" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        xml=[]
        for number,row in enumerate(rows,1):
            cells=[]
            for index,value in enumerate(row):
                coordinate=chr(65+index)+str(number)
                if isinstance(value,tuple) and value[0]=='formula':
                    cells.append(f'<c r="{coordinate}"><f>{escape(value[1])}</f><v>{ean(3)}</v></c>')
                elif isinstance(value,int):cells.append(f'<c r="{coordinate}"><v>{value}</v></c>')
                else:cells.append(f'<c r="{coordinate}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
            xml.append(f'<row r="{number}">'+''.join(cells)+'</row>')
        archive.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+''.join(xml)+'</sheetData></worksheet>')
    return out.getvalue()


def upload(client,raw,**kwargs):
    return client.post('/api/walmart/identifiers/spreadsheet',params={'filename':'EAN编码.xlsx',**kwargs},content=raw,
                       headers={'Content-Type':'application/octet-stream'})


def test_xlsx_column_identity_leading_zero_dedup_invalid_and_formula():
    raw=xlsx([['序号','UPC/EAN码','ASIN【UK】','品牌【UK】'],['1',ean(1),'B000000001','Another'],
              ['2','012345678905','',''],['3','0012345678905','',''],['4','1234567890123','',''],
              ['5',('formula','1+1'),'',''],['6',int(ean(2)),'','']])
    items,report=parse_spreadsheet(raw,'table.xlsx')
    assert [i['value'] for i in items]==[ean(1),'012345678905',ean(2)]
    assert [i['type'] for i in items]==['EAN','UPC','EAN']
    assert report['valid']==3 and report['duplicate']==1 and report['invalid']==2
    assert report['columns']==[{'sheet':'编码表','column':2}]
    assert all(i['brand']=='' and '用户提供' in i['proof'] for i in items)
    assert code_text(12345678905,'000000000000')=='012345678905'
    assert code_text(1.25)=='' and code_text(True)==''


def test_csv_bom_single_column_and_unsafe_files():
    items,report=parse_spreadsheet(('\ufeffEAN\n'+ean(1)+'\n'+ean(2)).encode(),'a.csv')
    assert len(items)==2
    assert len(parse_spreadsheet((ean(1)+'\n'+ean(2)).encode(),'a.csv')[0])==2
    for raw,name in [(b'broken','a.xlsx'),(b'none','a.xls'),('序号'.encode(),'a.csv'),
                     (b'EAN,UPC\n'+ean(1).encode()+b',012345678905','a.csv'),(b'x'*(8*1024*1024+1),'a.xlsx')]:
        with pytest.raises(HTTPException):parse_spreadsheet(raw,name)


def test_preview_does_not_write_import_is_idempotent_and_reports_history(local):
    client,catalog=local;raw=xlsx([['序号','UPC/EAN码'],*[['1',ean(i)] for i in range(130)]])
    assert upload(client,raw,preview='true').json()['valid']==130
    assert client.get('/api/walmart/identifiers').json()['stats']['total']==0
    report=upload(client,raw).json()
    assert report['inserted']==130 and report['existing']==0
    assert upload(client,raw).json()['existing']==130
    page=client.get('/api/walmart/identifiers').json()
    assert page['stats']=={'total':130,'available':130,'assigned':0}
    assert len(page['items'])==100 and page['total']==130 and len(page['imports'])==2
    assert len(client.get('/api/walmart/identifiers?page=2').json()['items'])==30
    assert client.get('/api/walmart/identifiers',params={'q':ean(100)}).json()['total']==1
    row=pool_assignment_locked(catalog,'Amazon','one','Brand')
    repeated=upload(client,raw).json()
    assert repeated['inserted']==0 and repeated['existing']==130
    assert client.get('/api/walmart/identifiers?status=assigned').json()['items'][0]['source_sku']=='one'
    assert upload(client,raw,brand='Other').status_code==409
    assert client.get('/api/walmart/identifiers').json()['stats']['total']==130


def pool_assignment_locked(catalog,system,sku,brand):
    with catalog.connect() as db:
        write_lock(db)
        return pool_assignment(db,system,sku,brand)


def test_random_selection_concurrent_unique_retries_and_brands(local,monkeypatch):
    client,catalog=local
    raw=xlsx([['EAN'],*[[ean(i)] for i in range(30)]])
    assert upload(client,raw).status_code==200
    monkeypatch.setattr('catalog.listing_prerequisites.secrets.randbelow',lambda size:size-1)
    first=pool_assignment_locked(catalog,'Amazon','same','Brand')
    assert first['value']==ean(29) and first['brand']=='Brand' and first['source_sku']=='same'
    assert pool_assignment_locked(catalog,'Amazon','same','Brand')['value']==first['value']
    with ThreadPoolExecutor(max_workers=4) as pool:
        result=list(pool.map(lambda i:pool_assignment_locked(catalog,'Amazon',str(i),'Brand'),range(20)))
    assert len({r['value'] for r in result})==20 and first['value'] not in {r['value'] for r in result}
    other=pool_assignment_locked(catalog,'Amazon','same','Other')
    assert other['value']!=first['value']
    with catalog.connect() as db:
        assert db.execute("SELECT count(*) FROM listing_identifier_events WHERE action='assign'").fetchone()[0]==22


def test_prelisting_assigns_without_ai_preserves_source_confirm_cancel_retry_and_cross_store(local):
    client,catalog=local
    before=catalog.export();raw=xlsx([['EAN'],*[[ean(i)] for i in range(10)]])
    assert upload(client,raw).json()['inserted']==10
    settings=inputs(client)
    preview=client.post('/api/prelisting/preview',json=settings).json()
    assert all(v['assigned_identifier']['state']=='pending' for i in preview['items'] for v in i['variants'])
    assert client.get('/api/walmart/identifiers').json()['stats']['assigned']==0
    batch,payload=create(client,settings)
    values=[v['assigned_identifier']['value'] for i in batch['preview']['items'] for v in i['variants']]
    assert len(set(values))==3
    assert client.post('/api/prelisting/batches',json=payload).json()['id']==batch['id']
    assert client.get('/api/walmart/identifiers').json()['stats']['assigned']==3
    assert [r['identification']['value'] for r in default_rows(batch['preview'])]==values
    assert client.post(f"/api/prelisting/batches/{batch['id']}/action",json={'action':'confirm'}).status_code==200
    assert client.post(f"/api/prelisting/batches/{batch['id']}/action",json={'action':'cancel'}).status_code==200
    repeated,_=create(client,settings)
    assert [v['assigned_identifier']['value'] for i in repeated['preview']['items'] for v in i['variants']]==values
    second=next(s for s in client.get('/api/listing-stores').json() if s['name']=='MYZ-L-18')
    cross,_=create(client,settings | {'store_id':second['id']})
    assert [v['assigned_identifier']['value'] for i in cross['preview']['items'] for v in i['variants']]==values
    assert client.get('/api/walmart/identifiers').json()['stats']['assigned']==3
    assert catalog.export()==before


def test_empty_pool_never_reuses_code_and_replenishment_binds_only_missing(local):
    client,catalog=local
    assert upload(client,xlsx([['EAN'],[ean(1)]])).status_code==200
    batch,_=create(client,inputs(client))
    variants=[v for i in batch['preview']['items'] for v in i['variants']]
    assert sum(v['assigned_identifier']['state']=='exhausted' for v in variants)==2
    assert len([r for r in default_rows(batch['preview']) if r['identification']['value']])==1
    assert upload(client,xlsx([['EAN'],[ean(2)],[ean(3)]])).status_code==200
    for item in batch['preview']['items']:
        variant=item['variants'][0]
        assigned=pool_assignment_locked(catalog,item['source'],variant['sku'],batch['preview']['brand'])
        if variant['assigned_identifier']['value']:
            assert assigned['value']==variant['assigned_identifier']['value']
    assert client.get('/api/walmart/identifiers').json()['stats']['assigned']==3


def test_import_is_admin_only_and_requires_csrf(team):
    raw=xlsx([['EAN'],[ean(1)]])
    for role in ('viewer','editor'):
        client,_=login(team,role)
        assert upload(client,raw).status_code==403
    client,_=login(team)
    assert upload(client,raw).status_code==200
    assert client.post('/api/walmart/identifiers/spreadsheet?filename=a.xlsx',content=raw,headers={'X-CSRF-Token':''}).status_code==403


def test_manual_historical_code_is_not_drawn_for_another_product(local,monkeypatch):
    client,catalog=local
    raw=xlsx([['EAN'],[ean(1)],[ean(2)]])
    assert upload(client,raw).status_code==200
    batch,_=create(client,inputs(client))
    # All available codes were assigned by creation. A separate free imported
    # code that appears in a saved document must also be excluded from draws.
    reserved=ean(3)
    assert upload(client,xlsx([['EAN'],[reserved]])).status_code==200
    from catalog.listing_generation_jobs import initialize
    from catalog.walmart_listing_schema import DEFAULT_VERSION
    from catalog.prelisting import encoded
    with catalog.connect() as db:
        write_lock(db)
        row=default_rows(batch['preview'])[0]
        row['identification']={'mode':'identifier','type':'EAN','value':reserved,'submission_id':''}
        db.execute('INSERT INTO walmart_listing_documents VALUES(?,?,?,?,?,?,?)',
                   (batch['id'],1,DEFAULT_VERSION,encoded([row]),encoded(batch['preview']),'{}','now'))
    assert pool_assignment_locked(catalog,'Amazon','different','Brand') is None


def test_wrong_preview_hash_rolls_back_all_allocations(local):
    client,catalog=local
    assert upload(client,xlsx([['EAN'],[ean(1)],[ean(2)],[ean(3)]])).status_code==200
    params=inputs(client)
    import uuid
    response=client.post('/api/prelisting/batches',json=params | {'preview_hash':'0'*64,'request_key':str(uuid.uuid4())})
    assert response.status_code==409
    assert client.get('/api/walmart/identifiers').json()['stats']['assigned']==0
    assert client.get('/api/prelisting/batches').json()['total']==0


def test_old_batch_import_and_allocate_without_binding_or_ai(local):
    client,catalog=local
    batch,_=create(client,inputs(client))
    base=f"/api/prelisting/batches/{batch['id']}/walmart"
    doc=client.post(base+'/prepare').json()
    assert doc['identifier_pool']['total']==0
    assert all(not row['identification']['value'] for row in doc['rows'])
    source=catalog.export();before=copy.deepcopy(doc['rows'])
    assert upload(client,xlsx([['EAN']]+[[ean(i)] for i in range(5)])).status_code==200
    response=client.post(base+'/identifiers/allocate',json={'revision':doc['revision']})
    assert response.status_code==200,response.text
    result=response.json();updated=result['document']
    assert result['assigned']==3 and not result['missing']
    assert updated['revision']==doc['revision']+1 and not updated['validation']['current']
    values=[row['identification']['value'] for row in updated['rows']]
    assert len(set(values))==3 and all(len(value)==13 for value in values)
    assert client.get(base).json()['rows']==updated['rows']
    for old,new in zip(before,updated['rows']):
        assert {k:v for k,v in old.items() if k!='identification'}=={k:v for k,v in new.items() if k!='identification'}
    repeated=client.post(base+'/identifiers/allocate',json={'revision':updated['revision']}).json()
    assert repeated['assigned']==0 and repeated['document']['rows']==updated['rows']
    assert client.post(base+'/identifiers/allocate',json={'revision':doc['revision']}).status_code==409
    overview=client.get(f"/api/prelisting/batches/{batch['id']}").json()
    assert sorted(v['assigned_identifier']['value'] for i in overview['preview']['items'] for v in i['variants'])==sorted(values)
    assert catalog.export()==source


def test_prepare_old_batch_with_imported_pool_allocates_only_blanks(local):
    client,catalog=local
    batch,_=create(client,inputs(client));base=f"/api/prelisting/batches/{batch['id']}/walmart"
    doc=client.post(base+'/prepare').json()
    doc['rows'][0]['identification'].update(type='EAN',value=ean(99))
    doc['rows'][1]['identification'].update(mode='exemption',submission_id='approved-by-store')
    response=client.patch(base,json={k:doc[k] for k in ('revision','version','rows')})
    assert response.status_code==200,response.text
    assert upload(client,xlsx([['EAN'],[ean(1)]])).status_code==200
    response=client.post(base+'/prepare');assert response.status_code==200,response.text
    rows=response.json()['rows']
    assert rows[0]['identification']['value']==ean(99)
    assert rows[1]['identification']['submission_id']=='approved-by-store'
    assert rows[2]['identification']['value']==ean(1)
    assert client.get('/api/walmart/identifiers').json()['stats']['assigned']==1


def test_allocate_exhaustion_is_clear_and_cancelled_batch_is_blocked(local):
    client,catalog=local
    batch,_=create(client,inputs(client));base=f"/api/prelisting/batches/{batch['id']}/walmart"
    doc=client.post(base+'/prepare').json()
    result=client.post(base+'/identifiers/allocate',json={'revision':doc['revision']}).json()
    assert result['assigned']==0 and len(result['missing'])==3
    assert result['document']['identifier_pool']==dict(total=0,available=0)
    assert upload(client,xlsx([['EAN'],[ean(1)]])).status_code==200
    client.post(f"/api/prelisting/batches/{batch['id']}/action",json={'action':'cancel'})
    assert client.post(base+'/identifiers/allocate',json={'revision':doc['revision']}).status_code==409
    assert client.get('/api/walmart/identifiers').json()['stats']['assigned']==0


def test_allocator_cannot_modify_submitted_skus_or_consume_their_codes(publishing):
    client,catalog,*_,base,remote=publishing
    send(publishing,ready(publishing))
    doc=client.get(base).json()
    # Simulate a historical broken document; submitted attempt keeps its original identifiers.
    for row in doc['rows']:
        row['identification'].update(value='',submission_id='',mode='identifier')
    with catalog.connect() as db:
        db.execute('UPDATE walmart_listing_documents SET rows_json=? WHERE batch_id=?',
                   (json.dumps(doc['rows']),publishing[4]['id']))
    assert upload(client,xlsx([['EAN'],[ean(1)],[ean(2)]])).status_code==200
    result=client.post(base+'/identifiers/allocate',json={'revision':doc['revision']}).json()
    assert result['assigned']==0 and len(result['skipped'])==2
    assert result['document']['rows']==doc['rows']
    assert client.get('/api/walmart/identifiers').json()['stats']['assigned']==0


def test_allocator_stops_during_generation(publishing):
    from catalog.listing_generation_jobs import start_job
    from test_listing_generation import AI
    client,catalog,*_,base,remote=publishing
    job=start_job(catalog,client.app.state.walmart_publisher,publishing[4]['id'],ai=AI(),schedule=False)
    assert job['state']=='queued'
    doc=client.get(base).json()
    assert client.post(base+'/identifiers/allocate',json={'revision':doc['revision']}).status_code==409
