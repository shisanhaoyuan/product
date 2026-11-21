import json
import subprocess

import pytest

from catalog import amazon_browser
from catalog.images import HumanVerificationRequired
from catalog.source_capture import collect_quote_page
from catalog.amazon_browser_worker import verify_page, CaptureStopped
from test_source_capture import page, offer

ASIN='B000000070'


class ContinueButton:
    def __init__(self, label='Continue', href=None, visible=True):
        self.label=label;self.href=href;self.visible=visible
    def get_attribute(self,key):return self.href if key=='href' else None
    def inner_text(self,**kwargs):return self.label
    def is_visible(self):return self.visible
    def is_enabled(self):return True


def test_continue_interstitial_scope_and_button_selection():
    from types import SimpleNamespace
    from catalog.amazon_browser_worker import continue_gate
    button=ContinueButton()
    page=SimpleNamespace(url='https://www.amazon.com/errors/validateCaptcha', product=False,
                         buttons=[ContinueButton('Continue',visible=False),button])
    def locator(selector):
        return SimpleNamespace(count=lambda:int(page.product),all=lambda:page.buttons)
    page.locator=locator
    assert continue_gate(page) is button
    page.product=True
    assert continue_gate(page) is None
    page.product=False
    for url in ['https://www.amazon.com/ap/signin','https://other.example/continue',
                'https://www.amazon.com/gp/cart/view.html']:
        page.url=url
        assert continue_gate(page) is None
    page.url='https://www.amazon.com/errors/validateCaptcha'
    for label,href in [('Continue to checkout',None),('Continue','https://evil.example/'),
                       ('Continue','/ap/signin')]:
        page.buttons=[ContinueButton(label,href)]
        assert continue_gate(page) is None


@pytest.mark.parametrize('clears_on',[1,3,None])
def test_continue_clicks_share_three_attempt_budget_and_verify_asin(monkeypatch,clears_on):
    from types import SimpleNamespace
    from catalog import amazon_browser_worker as worker
    page=SimpleNamespace(url='https://www.amazon.com/dp/'+ASIN,blocked=True)
    calls=[];verified=[]
    def click(**kwargs):
        calls.append('click')
        if len(calls)==clears_on:page.blocked=False
    button=SimpleNamespace(click=click)
    page.wait_for_timeout=lambda _:None
    page.wait_for_load_state=lambda *a,**k:None
    monkeypatch.setattr(worker,'continue_gate',lambda p:button if p.blocked else None)
    monkeypatch.setattr(worker,'verify_page',lambda p,asin:verified.append(asin))
    def no_solver(*a,**k):raise AssertionError('Continue should use its existing button')
    report=dict(attempts=0)
    if clears_on:
        worker.verify_or_try(SimpleNamespace(page=page),ASIN,report,no_solver,lambda p:p.blocked)
        assert report['product_verified'] and report['challenge_cleared']
        assert set(verified)=={ASIN}
    else:
        with pytest.raises(worker.CaptureStopped,match='verification_required'):
            worker.verify_or_try(SimpleNamespace(page=page),ASIN,report,no_solver,lambda p:p.blocked)
        assert not verified
    assert report['attempts']==len(calls)==(clears_on or 3)


def blocked(asin):
    raise HumanVerificationRequired('Amazon 要求人机验证')


def test_http_fast_path_and_disabled_fallback(monkeypatch):
    monkeypatch.delenv('CATALOG_AMAZON_BROWSER_PYTHON',raising=False)
    html=page(ASIN)
    assert collect_quote_page(ASIN,lambda _:html)[2]=='server'
    with pytest.raises(HumanVerificationRequired):collect_quote_page(ASIN,blocked)


def test_browser_fallback_reuses_strict_quote_parser(monkeypatch):
    monkeypatch.setenv('CATALOG_AMAZON_BROWSER_PYTHON','configured')
    calls=[]
    def fallback(asin):
        calls.append(asin)
        return page(asin,offer(original='72.89'))
    monkeypatch.setattr(amazon_browser,'fetch_page',fallback)
    def dynamic(asin): return page(asin)+'<a id="aod-ingress-link">Other sellers on Amazon</a>'
    assert collect_quote_page(ASIN,blocked)[2]=='wraith_background'
    assert calls==[ASIN]
    calls.clear()
    html,quote,method=collect_quote_page(ASIN,dynamic)
    assert method=='wraith_background' and quote['original_price']=='72.89'
    assert quote['asin']==ASIN and calls==[ASIN]
    monkeypatch.setattr(amazon_browser,'fetch_page',lambda _:page('B999999999'))
    with pytest.raises(ValueError,match='ASIN'):collect_quote_page(ASIN,dynamic)
    monkeypatch.setattr(amazon_browser,'fetch_page',lambda _:'Robot Check')
    with pytest.raises(HumanVerificationRequired):collect_quote_page(ASIN,dynamic)


def test_dynamic_offer_page_gets_one_browser_attempt(monkeypatch):
    monkeypatch.setenv('CATALOG_AMAZON_BROWSER_PYTHON','configured')
    calls=[]
    html=page(ASIN)+'<a id="aod-ingress-link">Other sellers on Amazon</a>'
    def fallback(asin):
        calls.append(asin)
        return html
    monkeypatch.setattr(amazon_browser,'fetch_page',fallback)
    _,quote,method=collect_quote_page(ASIN,lambda _:html)
    assert quote['status']=='browser_required' and calls==[ASIN]
    assert method=='wraith_background'  # Still unavailable, never counted as a valid quote.


@pytest.fixture
def runner(monkeypatch,tmp_path):
    executable=tmp_path/'python';executable.touch()
    monkeypatch.setenv('CATALOG_AMAZON_BROWSER_PYTHON',str(executable))
    monkeypatch.setenv('CATALOG_AMAZON_BROWSER_CACHE',str(tmp_path/'cache'))
    killed=[];commands=[];result={'status':'ok','html':page(ASIN)}
    class Process:
        pid=123456789
        def __init__(self,command,**kwargs):
            from pathlib import Path
            commands.append((command,kwargs))
            Path(command[-1]).write_text(json.dumps(result))
        def wait(self,timeout=None):return 0
    monkeypatch.setattr(amazon_browser.subprocess,'Popen',Process)
    monkeypatch.setattr(amazon_browser.os,'killpg',lambda *args:killed.append(args))
    return result,commands,killed


def test_isolated_runner_and_redacted_error(runner):
    result,commands,killed=runner
    assert amazon_browser.fetch_page(ASIN)==page(ASIN)
    command,options=commands[0]
    assert command[2:4]==[ASIN,'camoufox']
    assert options['env']['XDG_CACHE_HOME'].endswith('/cache')
    assert killed and options['stdout']==subprocess.DEVNULL
    result.update(status='verification_required',html='private-session-data')
    with pytest.raises(HumanVerificationRequired,match='Wraith') as error:
        amazon_browser.fetch_page(ASIN)
    assert 'private-session-data' not in str(error.value)
    with pytest.raises(ValueError):amazon_browser.fetch_page('https://other.example')


def test_timeout_kills_owned_process_group(runner,monkeypatch):
    _,commands,killed=runner
    class Hung:
        pid=123456789
        def __init__(self,*args,**kwargs):pass
        def wait(self,timeout=None):
            if timeout:raise subprocess.TimeoutExpired('browser',timeout)
            return -9
    monkeypatch.setattr(amazon_browser.subprocess,'Popen',Hung)
    with pytest.raises(HumanVerificationRequired,match='超时'):amazon_browser.fetch_page(ASIN)
    assert killed
    # Semaphore was released even on timeout.
    assert amazon_browser._GATE.acquire(blocking=False)
    amazon_browser._GATE.release()


def test_worker_does_not_accept_success_cookie_or_wrong_product():
    class Nodes:
        def __init__(self,count=0,ids=None):self.n=count;self.ids=ids
        def count(self):return self.n
        def evaluate_all(self,_):return self.ids
    class Page:
        url='https://www.amazon.com/dp/'+ASIN
        captcha=False
        ids=[ASIN]
        def locator(self,selector):
            if selector=='input#ASIN':return Nodes(ids=self.ids)
            if selector=='#productTitle':return Nodes(count=1)
            return Nodes(count=int(self.captcha))
    p=Page();verify_page(p,ASIN)
    p.captcha=True
    with pytest.raises(CaptureStopped,match='verification_required'):verify_page(p,ASIN)
    p.captcha=False;p.ids=['B999999999']
    with pytest.raises(CaptureStopped,match='identity_mismatch'):verify_page(p,ASIN)
    p.url='https://other.example/dp/'+ASIN
    with pytest.raises(CaptureStopped,match='identity_mismatch'):verify_page(p,ASIN)


@pytest.mark.parametrize('clears_on,expected', [(1,1),(2,2),(3,3),(None,3)])
def test_verification_round_limit_and_early_success(monkeypatch, clears_on, expected):
    from types import SimpleNamespace
    from catalog import amazon_browser_worker as worker
    page=SimpleNamespace(url='https://www.amazon.com/dp/'+ASIN, blocked=True)
    session=SimpleNamespace(page=page)
    report=dict(attempts=0,challenge_seen=False,challenge_cleared=False,product_verified=False)
    calls=[]
    def clear(url, **kwargs):
        calls.append(url)
        assert kwargs['timeout']==20 and kwargs['session'] is session
        if len(calls)==clears_on:page.blocked=False
    monkeypatch.setattr(worker,'verify_page',lambda page,asin:None)
    if clears_on:
        worker.verify_or_try(session,ASIN,report,clear,lambda p:p.blocked)
        assert report['challenge_cleared'] and report['product_verified']
    else:
        with pytest.raises(worker.CaptureStopped,match='verification_required'):
            worker.verify_or_try(session,ASIN,report,clear,lambda p:p.blocked)
        assert not report['challenge_cleared']
    assert len(calls)==report['attempts']==expected
    if not clears_on:
        with pytest.raises(worker.CaptureStopped):
            worker.verify_or_try(session,ASIN,report,clear,lambda p:p.blocked)
        assert len(calls)==3  # Later stages do not reset the same child's counter.


def test_timeout_rounds_do_not_accept_wraith_success_alone(monkeypatch):
    from types import SimpleNamespace
    from catalog import amazon_browser_worker as worker
    class WaapChallengeTimeout(Exception): pass
    page=SimpleNamespace(url='https://www.amazon.com/dp/'+ASIN)
    def clear(*args,**kwargs): raise WaapChallengeTimeout()
    report=dict(attempts=0)
    with pytest.raises(worker.CaptureStopped,match='verification_required'):
        worker.verify_or_try(SimpleNamespace(page=page),ASIN,report,clear,lambda p:True)
    assert report['attempts']==3 and not report['challenge_cleared']


def test_wrong_product_after_verification_is_not_success(monkeypatch):
    from types import SimpleNamespace
    from catalog import amazon_browser_worker as worker
    page=SimpleNamespace(url='https://www.amazon.com/dp/'+ASIN,blocked=True)
    def clear(*a,**k):page.blocked=False
    def wrong(*a):raise worker.CaptureStopped('identity_mismatch')
    monkeypatch.setattr(worker,'verify_page',wrong)
    report=dict(attempts=0)
    with pytest.raises(worker.CaptureStopped,match='identity_mismatch'):
        worker.verify_or_try(SimpleNamespace(page=page),ASIN,report,clear,lambda p:p.blocked)
    assert not report['challenge_cleared'] and report['attempts']==1


def test_browser_report_reaches_quote_and_failure_diagnostics(runner,monkeypatch):
    result,_,_=runner
    result['html']=page(ASIN,offer(original='72.89'))
    result['wraith_test']=dict(attempts=2,challenge_seen=True,challenge_cleared=True,product_verified=True,status='ok')
    _,quote,_=collect_quote_page(ASIN,blocked)
    assert quote['wraith_test']['attempts']==2
    assert quote['wraith_test']['trigger']=='http_verification'
    result.update(status='verification_required')
    result['wraith_test'].update(attempts=3,challenge_cleared=False,status='verification_required')
    with pytest.raises(HumanVerificationRequired,match='3/3') as exc:
        collect_quote_page(ASIN,blocked)
    assert exc.value.wraith_test['attempts']==3


def test_new_child_gets_own_attempt_budget_and_no_challenge_never_calls_solver(monkeypatch):
    from types import SimpleNamespace
    from catalog import amazon_browser_worker as worker
    monkeypatch.setattr(worker,'verify_page',lambda page,asin:None)
    calls=[]
    def clear(url,**kwargs): calls.append(url)
    for asin in [ASIN,'B000000071']:
        report=dict(attempts=0)
        session=SimpleNamespace(page=SimpleNamespace(url='https://www.amazon.com/dp/'+asin))
        with pytest.raises(worker.CaptureStopped):
            worker.verify_or_try(session,asin,report,clear,lambda p:True)
        assert report['attempts']==3
    assert len(calls)==6 and calls.count(calls[0])==3
    report=dict(attempts=0)
    worker.verify_or_try(session,asin,report,clear,lambda p:False)
    assert report['attempts']==0 and len(calls)==6
