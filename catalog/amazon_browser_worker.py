"""Executed by the separate browser venv; no application packages or secrets needed."""
import json
import os
from contextlib import contextmanager
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, urljoin


class CaptureStopped(Exception):
    pass


def continue_gate(page):
    """Only a visible Amazon interstitial, never login/cart/product-page controls."""
    url = urlparse(page.url)
    if (url.scheme != 'https' or url.hostname != 'www.amazon.com'
            or re.search(r'^/(?:ap/|gp/(?:cart|buy)/|checkout)', url.path)
            or not hasattr(page, 'locator')):
        return None
    if page.locator('#productTitle, input[type="password"], input[type="email"], input#captchacharacters').count():
        return None
    for node in page.locator('button, input[type="submit"], input[type="button"], a').all():
        label = node.get_attribute('value') or node.inner_text(timeout=1000)
        if not re.fullmatch(r'\s*continue(?:\s+shopping|\s+to\s+amazon)?\s*', label, re.I):
            continue
        if not node.is_visible() or not node.is_enabled():
            continue
        destination = node.get_attribute('href') or node.get_attribute('formaction')
        if destination:
            target = urlparse(urljoin(page.url, destination))
            if target.scheme != 'https' or target.hostname != 'www.amazon.com':
                continue
            if re.search(r'^/(?:ap/|gp/(?:cart|buy)/|checkout)', target.path):
                continue
        return node
    return None


def verify_page(page, asin):
    url = urlparse(page.url)
    if url.hostname != 'www.amazon.com' or not re.fullmatch(r'/(?:[^/]+/)?dp/' + asin + '/?', url.path):
        if re.search(r'captcha|challenge|checkpoint', page.url, re.I):
            raise CaptureStopped('verification_required')
        raise CaptureStopped('identity_mismatch')
    if page.locator('input#captchacharacters, form[action*="validateCaptcha"]').count():
        raise CaptureStopped('verification_required')
    ids = page.locator('input#ASIN').evaluate_all('(els) => els.map(e => e.value)')
    if not ids or set(ids) != {asin}:
        raise CaptureStopped('identity_mismatch')
    if not page.locator('#productTitle').count():
        raise CaptureStopped('failed')


def challenge_visible(page):
    if continue_gate(page) is not None:
        return True
    from wraith._press_hold import inspect_press_hold
    html = page.content()
    active, _ = inspect_press_hold(page, html)
    text = page.locator('body').inner_text(timeout=3000)
    return bool(active or re.search(r'captcha|challenge|checkpoint', urlparse(page.url).path, re.I)
                or page.locator('input#captchacharacters, form[action*="validateCaptcha"]').count()
                or re.search(r'robot check|enter the characters you see below|verify you are human|not a robot', text, re.I))


def verify_or_try(session, asin, report, clear, detect=challenge_visible):
    """Three bounded Wraith rounds per child, shared across all page stages."""
    page = session.page
    while True:
        gate = continue_gate(page)
        if gate is None and not detect(page):
            break
        if urlparse(page.url).hostname != 'www.amazon.com':
            raise CaptureStopped('identity_mismatch')
        report['challenge_seen'] = True
        report['challenge_cleared'] = False
        report['product_verified'] = False
        if report['attempts'] >= 3:
            raise CaptureStopped('verification_required')
        report['attempts'] += 1
        try:
            if gate is not None:
                gate.click(timeout=5000)
                page.wait_for_timeout(1000)
                page.wait_for_load_state('domcontentloaded', timeout=15000)
            else:
                clear(f'https://www.amazon.com/dp/{asin}?th=1&psc=1', session=session,
                      timeout=20, settle=1, press_hold=True)
        except Exception as exc:
            if type(exc).__name__ not in ('WaapChallengeTimeout', 'TimeoutError'):
                raise
            # A cookie or return code is insufficient; inspect the page below.
        if continue_gate(page) is None and not detect(page):
            verify_page(page, asin)
            report['challenge_cleared'] = True
            break
    verify_page(page, asin)
    report['product_verified'] = True


def ensure_location(page):
    def verified():
        nodes = page.locator('#glow-ingress-line2, #contextualIngressPtLabel_deliveryShortLine')
        return any(re.search(r'\b10001\b', text) for text in nodes.all_text_contents())
    if verified():
        return
    try:
        page.locator('#nav-global-location-popover-link').click(timeout=5000)
        page.locator('#GLUXZipUpdateInput').fill('10001', timeout=5000)
        page.locator('#GLUXZipUpdate').click(timeout=5000)
        page.wait_for_timeout(1200)
        done = page.locator('button[name="glowDoneButton"], #GLUXConfirmClose').first
        if done.count() and done.is_visible():
            done.click(timeout=3000)
        page.reload(wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(1000)
    except Exception:
        raise CaptureStopped('location_unverified') from None
    if not verified():
        raise CaptureStopped('location_unverified')


@contextmanager
def persistent_profile():
    # This is a dedicated service profile, never a person's Chrome directory.
    directory = os.environ.get('CATALOG_AMAZON_BROWSER_PROFILE')
    if not directory:
        yield None
        return
    import fcntl
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    with (path / '.jiwu.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CaptureStopped('browser_busy') from None
        try:
            yield str(path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def collect(asin, engine, report):
    with persistent_profile() as profile:
        return collect_in_profile(asin, engine, report, profile)


def collect_in_profile(asin, engine, report, profile):
    from wraith.engine import launch, clear_challenge
    session = None
    try:
        session = launch(engine=engine, headless=True, geoip=False, profile_dir=profile)
        page = session.page
        page.set_default_timeout(5000)
        page.set_default_navigation_timeout(25000)
        url = f'https://www.amazon.com/dp/{asin}?th=1&psc=1'
        response = page.goto(url, wait_until='domcontentloaded')
        if (response is None or not response.ok) and not challenge_visible(page):
            raise CaptureStopped('rate_limited' if response and response.status in (403,429) else 'failed')
        # Wraith success alone is not proof of Amazon identity or a usable quote.
        verify_or_try(session, asin, report, clear_challenge)
        location_verified = True
        try:
            try:
                ensure_location(page)
            except CaptureStopped:
                if challenge_visible(page):
                    verify_or_try(session, asin, report, clear_challenge)
                    ensure_location(page)
                else:
                    raise
        except CaptureStopped as exc:
            if str(exc) != 'location_unverified':
                raise
            location_verified = False
        report['location_verified'] = location_verified
        verify_or_try(session, asin, report, clear_challenge)
        helper = Path(__file__).parent.parent / 'browser-helper'
        page.evaluate('() => {' + (helper / 'offers.js').read_text() +
                      '; globalThis.jiwuReadOffer = globalThis.jiwuOfferReader(); }')
        if location_verified:
            for _ in range(27):
                ready = page.evaluate('() => { globalThis.jiwuOfferHTML = globalThis.jiwuReadOffer(); return !!globalThis.jiwuOfferHTML; }')
                if ready:
                    break
                page.wait_for_timeout(600)
            verify_or_try(session, asin, report, clear_challenge)
        page.evaluate('() => {' + (helper / 'gallery.js').read_text() + '}')
        # Product media may arrive after the offer; wait briefly before snapshotting.
        for _ in range(10):
            if page.evaluate('() => globalThis.jiwuImageGallery().available'):
                break
            page.wait_for_timeout(600)
        verify_or_try(session, asin, report, clear_challenge)
        # Send only product/offer markup, not scripts, account menus, tokens or cookies.
        html = page.evaluate('() => {' + (helper / 'product.js').read_text() +
                             (helper / 'gallery.js').read_text() + r'''
          const identity = document.createElement('input'); identity.id = 'ASIN';
          identity.setAttribute('value', document.querySelector('input#ASIN')?.value || '');
          const offer = globalThis.jiwuOfferHTML || globalThis.jiwuReadOffer();
          return identity.outerHTML + offer + globalThis.jiwuProductDetails() + globalThis.jiwuImageGallery().html;
        }''')
        if len(html.encode()) > 6 * 1024 * 1024:
            raise CaptureStopped('failed')
        return dict(status='ok', html=html)
    finally:
        if session is not None:
            session.close()


def main():
    asin, engine, output = sys.argv[1:]
    if not re.fullmatch(r'[A-Z0-9]{10}', asin) or engine not in ('camoufox', 'chromium'):
        raise SystemExit(2)
    started = time.monotonic()
    report = dict(attempts=0, challenge_seen=False, challenge_cleared=False, product_verified=False)
    try:
        result = collect(asin, engine, report)
    except CaptureStopped as exc:
        result = dict(status=str(exc))
    except ImportError:
        result = dict(status='unavailable')
    except Exception as exc:
        name = type(exc).__name__
        status = ('unavailable' if name in ('EngineUnavailableError', 'PlaywrightVersionError') else
                  'rate_limited' if name in ('WaapRateLimitedError', 'WaapHardBlockError') else
                  'verification_required' if name == 'WaapChallengeTimeout' else 'failed')
        result = dict(status=status)
    report.update(elapsed_seconds=round(time.monotonic()-started, 1), status=result['status'])
    result['wraith_test'] = report
    Path(output).write_text(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
