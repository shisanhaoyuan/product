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
        if location_verified:
            regular = page.get_by_text(re.compile(r'^Regular\s+Price$', re.I))
            for node in regular.all():
                if node.is_visible():
                    node.click();page.wait_for_timeout(600);break
            entrances = page.locator('#aod-ingress-link, #buybox-see-all-buying-choices a, [data-action="show-all-offers-display"]')
            for node in entrances.all():
                if node.is_visible() and not page.locator('#aod-offer-list:visible').count():
                    node.click()
                    page.locator('#aod-offer-list').wait_for(state='visible', timeout=8000)
                    break
            page.wait_for_timeout(1000)
            verify_or_try(session, asin, report, clear_challenge)
        # Send only product/offer markup, not scripts, account menus, tokens or cookies.
        html = page.evaluate(r'''() => {
  function initialGallery(script) {
    const match = /["']colorImages["']\s*:\s*\{\s*["']initial["']\s*:\s*/.exec(script);
    if (!match) return '';
    const payload = script.slice(match.index + match[0].length);
    const wrapped = /^A\.\$\.parseJSON\(\s*('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")\s*\)/.exec(payload);
    if (wrapped) return wrapped[0];
    if (payload[0] !== '[') return '';
    let depth = 0, quoted = false, escaped = false;
    for (let i = 0; i < payload.length; i++) {
      const char = payload[i];
      if (quoted) {
        if (escaped) escaped = false;
        else if (char === '\\') escaped = true;
        else if (char === '"') quoted = false;
      } else if (char === '"') quoted = true;
      else if (char === '[') depth++;
      else if (char === ']' && --depth === 0) return payload.slice(0, i + 1);
    }
    return '';
  }


    function excludedOfferNode(node) {
      for (let current = node; current; current = current.parentElement) {
        if (/primeSavings|recommend|sponsored/i.test(current.id || '')) return true;
        const style = getComputedStyle(current);
        if (current.hidden || style.display === 'none' || style.visibility === 'hidden') return true;
      }
      return false;
    }
    function offerClone(node) {
      const clone = node.cloneNode(true);
      const originals = [...node.querySelectorAll('*')];
      const copies = [...clone.querySelectorAll('*')];
      originals.forEach((child, index) => {
        if (excludedOfferNode(child)) copies[index].remove();
      });
      return clone;
    }

          const selectors = ['input#ASIN', '#productTitle', '#feature-bullets', '#productDescription',
            '#productOverview_feature_div', '#productDetails_techSpec_section_1', '#productDetails_detailBullets_sections1',
            '#glow-ingress-line2', '#contextualIngressPtLabel_deliveryShortLine',
            '#corePriceDisplay_desktop_feature_div', '#corePrice_feature_div', '#desktop_buybox', '#buybox',
            '#merchant-info', '#tabular-buybox', '#deliveryBlockMessage',
            '[id^="mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE"]', '#aod-offer-list', '#aod-ingress-link'];
          const nodes = [...new Set(selectors.flatMap(s => [...document.querySelectorAll(s)]))].filter(node => !excludedOfferNode(node));
          const root = document.createElement('section'); root.id = 'catalog-main-offer';
          for (const node of nodes) {
            if (nodes.some(other => other !== node && other.contains(node))) continue;
            root.append(offerClone(node));
          }
          for (const node of root.querySelectorAll('script, style, [id*="recommend"], [id*="sponsored"]')) node.remove();
          const gallery = [...document.scripts].map(script => initialGallery(script.textContent)).find(Boolean);
          const landing = document.querySelector('img#landingImage');
          const image = document.createElement('img'); image.id = 'landingImage';
          for (const name of ['data-old-hires', 'data-a-dynamic-image']) {
            if (landing?.hasAttribute(name)) image.setAttribute(name, landing.getAttribute(name));
          }
          return root.outerHTML + image.outerHTML + (gallery ? `'colorImages': {'initial': ${gallery}}` : '');
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
