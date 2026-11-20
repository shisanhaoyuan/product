"""Executed by the separate browser venv; no application packages or secrets needed."""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


class CaptureStopped(Exception):
    pass


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


def collect(asin, engine):
    from wraith.engine import launch, clear_challenge
    session = None
    try:
        session = launch(engine=engine, headless=True, geoip=True)
        page = session.page
        page.set_default_timeout(5000)
        page.set_default_navigation_timeout(25000)
        url = f'https://www.amazon.com/dp/{asin}?th=1&psc=1'
        clear_challenge(url, session=session, timeout=30, settle=1)
        # Wraith success alone is not proof of Amazon identity or a usable quote.
        verify_page(page, asin)
        ensure_location(page)
        verify_page(page, asin)
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
        verify_page(page, asin)
        # Send only product/offer markup, not scripts, account menus, tokens or cookies.
        html = page.evaluate('''() => {
          const selectors = ['input#ASIN', '#productTitle', '#feature-bullets', '#productDescription',
            '#productOverview_feature_div', '#productDetails_techSpec_section_1', '#productDetails_detailBullets_sections1',
            '#glow-ingress-line2', '#contextualIngressPtLabel_deliveryShortLine',
            '#corePriceDisplay_desktop_feature_div', '#corePrice_feature_div', '#desktop_buybox', '#buybox',
            '#merchant-info', '#tabular-buybox', '#deliveryBlockMessage',
            '[id^="mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE"]', '#aod-offer-list', '#aod-ingress-link'];
          const nodes = [...new Set(selectors.flatMap(s => [...document.querySelectorAll(s)]))];
          const root = document.createElement('section'); root.id = 'catalog-main-offer';
          for (const node of nodes) {
            if (nodes.some(other => other !== node && other.contains(node))) continue;
            root.append(node.cloneNode(true));
          }
          for (const node of root.querySelectorAll('script, style, [id*="recommend"], [id*="sponsored"]')) node.remove();
          return root.outerHTML;
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
    try:
        result = collect(asin, engine)
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
    Path(output).write_text(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
