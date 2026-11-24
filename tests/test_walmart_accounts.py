import base64
import json
import os
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from catalog.app import create_app
from catalog.credential_vault import initialize_key, read_key, encrypt, decrypt, VaultError
from catalog.walmart_client import WalmartVerifier, WalmartError
from test_auth import team, login, ORIGIN

CLIENT = 'dummy-walmart-client-12345'
SECRET = 'dummy-walmart-secret-67890'
TOKEN = 'dummy-walmart-token'
INPUT = {'client_id': CLIENT, 'client_secret': SECRET}


@pytest.fixture
def vault(tmp_path, monkeypatch):
    path = tmp_path/'master.key'
    monkeypatch.setenv('CATALOG_CREDENTIAL_KEY_FILE', str(path))
    initialize_key(path)
    return path


class Remote:
    def __init__(self):
        self.partner = '100009'; self.failure = ''; self.calls = []; self.hook = None

    def handler(self, request):
        self.calls.append(request)
        assert request.url.host == 'marketplace.walmartapis.com'
        assert request.url.scheme == 'https'
        assert request.headers['WM_SVC.NAME'] == 'Walmart Marketplace'
        uuid.UUID(request.headers['WM_QOS.CORRELATION_ID'])
        if self.hook:
            hook, self.hook = self.hook, None
            hook()
        if self.failure:
            if self.failure == 'network': raise httpx.ConnectError(SECRET, request=request)
            if self.failure == 'invalid': return httpx.Response(200, text=SECRET)
            return httpx.Response(int(self.failure),text=SECRET,headers={'location':'https://evil.example/'+SECRET})
        if request.url.path == '/v3/token':
            assert request.method == 'POST'
            assert request.headers['authorization'] == 'Basic '+base64.b64encode((CLIENT+':'+SECRET).encode()).decode()
            assert request.content == b'grant_type=client_credentials'
            return httpx.Response(200,json={'access_token':TOKEN,'expires_in':900,'token_type':'Bearer'})
        assert request.method == 'GET'
        assert request.headers['WM_SEC.ACCESS_TOKEN'] == TOKEN
        if request.url.path == '/v3/token/detail':
            return httpx.Response(200,json={'is_valid':True,'scopes':{'item':'full_access','inventory':'view_only','orders':'no_access','arbitrary':SECRET}})
        assert request.url.path == '/v3/settings/partnerprofile'
        return httpx.Response(200,json={'partner':{'partnerId':self.partner,'partnerName':'Example Legal',
            'partnerDisplayName':'Example Shop','partnerStoreId':'1009','businessRegNumber':'DO-NOT-SAVE-TAX'},
            'configurations':[{'configurationName':'ACCOUNT','configuration':{'status':'ACTIVE'}}]})

    def install(self, app):
        app.state.walmart_verifier = WalmartVerifier(httpx.MockTransport(self.handler))
        return self


@pytest.fixture
def local(tmp_path, vault):
    app=create_app(tmp_path/'accounts.db'); remote=Remote().install(app); c=TestClient(app)
    stores=c.get('/api/listing-stores').json()
    return app,c,remote,[s['id'] for s in stores[:2]]


def preview(c,store):
    return c.post('/api/walmart/accounts/preview',json={'store_id':store,**INPUT})


def bind(c,store):
    result=preview(c,store)
    assert result.status_code==200,result.text
    result=c.post(f'/api/walmart/accounts/{store}/bind',json={'verification_id':result.json()['verification_id']})
    assert result.status_code==200,result.text
    return result.json()


def test_vault_authenticated_store_binding_file_permissions_and_no_overwrite(vault,monkeypatch,tmp_path):
    original=vault.read_bytes()
    cipher=encrypt(21,INPUT)
    assert CLIENT not in cipher and SECRET not in cipher
    assert decrypt(21,cipher)==INPUT
    with pytest.raises(VaultError): decrypt(22,cipher)
    with pytest.raises(VaultError): decrypt(21,cipher[:-5]+'AAAAA')
    with pytest.raises(FileExistsError): initialize_key(vault)
    assert vault.read_bytes()==original
    vault.chmod(0o644)
    if os.name!='nt':
        with pytest.raises(VaultError): read_key()
    vault.chmod(0o600)
    other=tmp_path/'other.key';initialize_key(other)
    monkeypatch.setenv('CATALOG_CREDENTIAL_KEY_FILE',str(other))
    with pytest.raises(VaultError): decrypt(21,cipher)


def test_bind_reopen_redaction_and_audit(local):
    app,c,remote,ids=local;store=ids[0]
    draft=preview(c,store).json()
    assert draft['profile']['partnerId']=='100009'
    assert not c.get('/api/walmart/accounts').json()['stores'][0]['account']
    result=c.post(f'/api/walmart/accounts/{store}/bind',json={'verification_id':draft['verification_id']})
    assert result.status_code==200,result.text
    account=result.json();assert account['bound'] and account['revision']==1
    assert account['scopes']=={'item':'full_access','inventory':'view_only','orders':'no_access'}
    assert account['profile']['accountStatus']=='ACTIVE'
    for secret in (CLIENT,SECRET,TOKEN,'DO-NOT-SAVE-TAX'):
        assert secret not in result.text
    with app.state.catalog.connect() as db:
        row=db.execute('SELECT * FROM walmart_accounts WHERE store_id=?',(store,)).fetchone()
        values=' '.join(str(v) for v in dict(row).values())
        for secret in (CLIENT,SECRET,TOKEN,'DO-NOT-SAVE-TAX'): assert secret not in values
        assert decrypt(store,row['encrypted_credentials'])==INPUT
        assert not db.execute('SELECT 1 FROM walmart_binding_previews').fetchone()
        assert [r['action'] for r in db.execute('SELECT action FROM walmart_account_events ORDER BY at')]==['preview','bind']
    reopened=TestClient(create_app(app.state.catalog.path))
    assert next(s for s in reopened.get('/api/walmart/accounts').json()['stores'] if s['store_id']==store)['account']['bound']
    assert c.post(f'/api/walmart/accounts/{store}/bind',json={'verification_id':draft['verification_id']}).status_code==409
    assert [r.url.path for r in remote.calls]==['/v3/token','/v3/token/detail','/v3/settings/partnerprofile']


def test_failed_replacement_preserves_working_credentials_and_reverify_marks_real_failure(local):
    app,c,remote,ids=local;store=ids[0];before=bind(c,store)
    remote.failure='401'
    response=preview(c,store)
    assert response.status_code==400 and SECRET not in response.text
    account=next(s for s in c.get('/api/walmart/accounts').json()['stores'] if s['store_id']==store)['account']
    assert account==before
    response=c.post(f'/api/walmart/accounts/{store}/verify',json={'revision':before['revision']})
    assert response.status_code==200
    failed=response.json()
    assert failed['bound'] and failed['status']=='error' and failed['last_error']
    assert failed['last_verified_at']==before['last_verified_at']
    assert SECRET not in response.text
    remote.failure=''
    assert c.post(f'/api/walmart/accounts/{store}/verify',json={'revision':failed['revision']}).json()['status']=='verified'


def test_duplicate_remote_account_identity_and_unbind_rebind(local):
    app,c,remote,ids=local;a,b=ids;before=bind(c,a)
    assert preview(c,b).status_code==409
    remote.partner='other-partner'
    assert preview(c,a).status_code==409
    mismatched=c.post(f'/api/walmart/accounts/{a}/verify',json={'revision':1}).json()
    assert mismatched['status']=='error' and mismatched['profile']['partnerId']=='100009'
    assert c.post(f'/api/walmart/accounts/{a}/unbind',json={'revision':1}).status_code==409
    # Even if deactivated, the administrator can remove local credentials.
    c.patch('/api/listing-stores/'+str(a),json={'active':False})
    assert c.post(f'/api/walmart/accounts/{a}/unbind',json={'revision':mismatched['revision']}).json()['bound'] is False
    remote.partner='100009'
    assert bind(c,b)['profile']['partnerId']=='100009'
    with app.state.catalog.connect() as db:
        assert db.execute('SELECT encrypted_credentials FROM walmart_accounts WHERE store_id=?',(a,)).fetchone()[0]==''


def test_preview_expires_other_store_replay_and_concurrent_revision(local):
    app,c,remote,ids=local;a,b=ids
    draft=preview(c,a).json(); payload={'verification_id':draft['verification_id']}
    assert c.post(f'/api/walmart/accounts/{b}/bind',json=payload).status_code==409
    with app.state.catalog.connect() as db:
        db.execute('UPDATE walmart_binding_previews SET expires_at=0')
    assert c.post(f'/api/walmart/accounts/{a}/bind',json=payload).status_code==409
    bind(c,a)
    draft=preview(c,a).json()
    assert c.post(f'/api/walmart/accounts/{a}/verify',json={'revision':1}).json()['revision']==2
    assert c.post(f'/api/walmart/accounts/{a}/bind',json={'verification_id':draft['verification_id']}).status_code==409
    # An in-flight verifier must not restore an account unbound by a second request.
    remote.hook=lambda:c.post(f'/api/walmart/accounts/{a}/unbind',json={'revision':2})
    assert c.post(f'/api/walmart/accounts/{a}/verify',json={'revision':2}).status_code==409
    assert not next(s for s in c.get('/api/walmart/accounts').json()['stores'] if s['store_id']==a)['account']['bound']


def test_missing_key_safe_input_errors_and_store_guards(local,vault):
    app,c,remote,ids=local;a,b=ids
    vault.unlink()
    assert not c.get('/api/walmart/accounts').json()['vault']['ready']
    assert preview(c,a).status_code==400
    assert not remote.calls
    for body in [{**INPUT,'store_id':a,'endpoint':'https://evil.example'},
                 {'store_id':a,'client_id':CLIENT,'client_secret':{'leak':SECRET}},
                 {'store_id':a,'client_id':CLIENT,'client_secret':SECRET+'\n'}]:
        response=c.post('/api/walmart/accounts/preview',json=body)
        assert response.status_code==422 and CLIENT not in response.text and SECRET not in response.text
    initialize_key(vault)
    assert preview(c,99999).status_code==404
    c.patch('/api/listing-stores/'+str(a),json={'active':False})
    assert preview(c,a).status_code==409


def test_admin_only_csrf_and_preview_owner(team,vault):
    Remote().install(team);admin,_=login(team)
    store=admin.get('/api/listing-stores').json()[0]['id']
    for role in ['editor','viewer']:
        c,_=login(team,role)
        assert c.get('/api/walmart/accounts').status_code==403
        assert preview(c,store).status_code==403
        assert c.post(f'/api/walmart/accounts/{store}/unbind',json={'revision':0}).status_code==403
    assert admin.post('/api/walmart/accounts/preview',json={'store_id':store,**INPUT},headers={'X-CSRF-Token':''}).status_code==403
    assert admin.post('/api/walmart/accounts/preview',json={'store_id':store,**INPUT},headers={'Origin':'https://evil.example'}).status_code==403
    draft=preview(admin,store).json()
    with team.state.catalog.connect() as db: db.execute("UPDATE users SET role='admin' WHERE username='editor'")
    other,_=login(team,'editor')
    assert other.post(f'/api/walmart/accounts/{store}/bind',json={'verification_id':draft['verification_id']}).status_code==409
    assert admin.post(f'/api/walmart/accounts/{store}/bind',json={'verification_id':draft['verification_id']}).status_code==200
    outsider=TestClient(team,base_url=ORIGIN)
    assert outsider.get('/api/walmart/accounts').status_code==401


@pytest.mark.parametrize('failure',['401','403','429','500','302','invalid','network'])
def test_official_only_http_errors_redacted_no_redirect(failure):
    remote=Remote();remote.failure=failure
    with pytest.raises(WalmartError) as e: WalmartVerifier(httpx.MockTransport(remote.handler)).verify(**INPUT)
    assert SECRET not in str(e.value) and len(remote.calls)==1


def test_missing_identity_or_invalid_token_never_bound():
    for invalid in ['profile','token','scopes']:
        remote=Remote()
        def handler(request):
            response=remote.handler(request)
            if invalid=='profile' and request.url.path.endswith('partnerprofile'):
                return httpx.Response(200,json={'partner':{'partnerDisplayName':'Missing ID'}})
            if invalid=='token' and request.url.path=='/v3/token':
                return httpx.Response(200,json={'access_token':TOKEN,'expires_in':'NaN'})
            if invalid=='scopes' and request.url.path.endswith('/detail'):
                return httpx.Response(200,json={'is_valid':False})
            return response
        with pytest.raises(WalmartError): WalmartVerifier(httpx.MockTransport(handler)).verify(**INPUT)


def test_successful_key_replacement_only_after_confirmation(local):
    app,c,remote,ids=local;store=ids[0]
    original=bind(c,store)
    next_id='new-dummy-client-23456'; next_secret='new-dummy-secret-78901'
    class Replacement:
        def verify(self,client_id,client_secret):
            assert (client_id,client_secret)==(next_id,next_secret)
            return {'profile':original['profile'],'scopes':{'item':'full_access'}}
    app.state.walmart_verifier=Replacement()
    preview=c.post('/api/walmart/accounts/preview',json={'store_id':store,'client_id':next_id,'client_secret':next_secret}).json()
    with app.state.catalog.connect() as db:
        assert decrypt(store,db.execute('SELECT encrypted_credentials FROM walmart_accounts').fetchone()[0])==INPUT
    confirmed=c.post(f'/api/walmart/accounts/{store}/bind',json={'verification_id':preview['verification_id']})
    assert confirmed.status_code==200 and confirmed.json()['revision']==2
    with app.state.catalog.connect() as db:
        assert decrypt(store,db.execute('SELECT encrypted_credentials FROM walmart_accounts').fetchone()[0])=={'client_id':next_id,'client_secret':next_secret}
    assert next_id not in confirmed.text and next_secret not in confirmed.text


def test_canada_binding_reverify_and_market_isolation(local):
    app,c,remote,ids=local;store,other=ids
    draft=c.post('/api/walmart/accounts/preview',json={
        'store_id':store,'marketplace':'CA',**INPUT})
    assert draft.status_code==200,draft.text
    assert draft.json()['marketplace']=='CA'
    assert draft.json()['profile']['identityVerified'] is False
    assert draft.json()['profile']['partnerId']==''
    result=c.post(f'/api/walmart/accounts/{store}/bind',json={
        'verification_id':draft.json()['verification_id']})
    assert result.status_code==200,result.text
    assert result.json()['marketplace']=='CA' and result.json()['bound']
    with app.state.catalog.connect() as db:
        row=db.execute('SELECT * FROM walmart_accounts WHERE store_id=?',(store,)).fetchone()
        assert decrypt(store,row['encrypted_credentials'],'CA')==INPUT
        with pytest.raises(VaultError):
            decrypt(store,row['encrypted_credentials'],'US')
        from catalog.walmart_publishing import binding
        _, bound=binding(db,store)
        assert bound['marketplace']=='CA' and bound['_ca_bundle']['version']=='3.16'
    verified=c.post(f'/api/walmart/accounts/{store}/verify',json={'revision':1})
    assert verified.status_code==200 and verified.json()['status']=='verified'
    assert [r.url.path for r in remote.calls]==['/v3/token','/v3/token/detail']*2
    assert all(r.headers['WM_MARKET']=='ca' for r in remote.calls)
    duplicate=c.post('/api/walmart/accounts/preview',json={
        'store_id':other,'marketplace':'CA',**INPUT})
    assert duplicate.status_code==409
    # A bound store cannot silently change markets, even with valid credentials.
    assert preview(c,store).status_code==409
    assert c.post(f'/api/walmart/accounts/{store}/unbind',json={'revision':2}).status_code==200
    assert bind(c,store)['marketplace']=='US'
    assert all(r.headers['WM_MARKET']=='us' for r in remote.calls[-3:])


def test_legacy_us_binding_survives_market_migration(local):
    app,c,remote,ids=local;store=ids[0]
    before=bind(c,store)
    from catalog.walmart_accounts import ACCOUNT_TABLE, initialize
    with app.state.catalog.connect() as db:
        ciphertext=db.execute('SELECT encrypted_credentials FROM walmart_accounts').fetchone()[0]
        db.execute(ACCOUNT_TABLE.replace('walmart_accounts(', 'legacy_accounts(').replace(
            "marketplace IN ('US','CA')", "marketplace='US'"))
        db.execute('INSERT INTO legacy_accounts SELECT * FROM walmart_accounts')
        db.execute('DROP TABLE walmart_accounts')
        db.execute('ALTER TABLE legacy_accounts RENAME TO walmart_accounts')
        db.execute('DELETE FROM migrations WHERE version=22')
        initialize(db)
        initialize(db)
        row=db.execute('SELECT * FROM walmart_accounts').fetchone()
        assert row['encrypted_credentials']==ciphertext
        assert decrypt(store,ciphertext)==INPUT
        assert row['revision']==before['revision'] and row['marketplace']=='US'
        assert "'CA'" in db.execute("SELECT sql FROM sqlite_master WHERE name='walmart_accounts'").fetchone()[0]
