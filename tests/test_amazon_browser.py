import json
import subprocess

import pytest

from catalog import amazon_browser
from catalog.images import HumanVerificationRequired
from catalog.source_capture import collect_quote_page
from catalog.amazon_browser_worker import verify_page, CaptureStopped
from test_source_capture import page, offer

ASIN='B000000070'


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
    with pytest.raises(HumanVerificationRequired):collect_quote_page(ASIN,blocked)
    assert not calls
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
