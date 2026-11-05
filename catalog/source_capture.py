"""ASIN-bound source observations, stored separately from ERP prices and evidence."""
import json
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request

from .database import write_lock
from .images import (BrowserCaptureInput, CaptureInput, HumanVerificationRequired,
                     ProductHTML, fetch_amazon_page, needs_verification)


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS source_captures(
        id TEXT PRIMARY KEY, listing_id TEXT NOT NULL REFERENCES listings(id),
        variant_sku TEXT NOT NULL, asin TEXT NOT NULL, captured_at TEXT NOT NULL,
        actor_id INTEGER, data_json TEXT NOT NULL)''')
    db.execute('''CREATE INDEX IF NOT EXISTS source_capture_target
        ON source_captures(listing_id,variant_sku,asin,captured_at)''')
    db.execute('INSERT INTO migrations VALUES(8) ON CONFLICT DO NOTHING')


class Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.parts = []

    def hidden(self):
        style = self.attrs.get('style', '').replace(' ', '').lower()
        return ('hidden' in self.attrs
                or 'display:none' in style or 'visibility:hidden' in style
                or 'aok-hidden' in self.attrs.get('class', '').split())

    def nodes(self):
        if self.hidden():
            return
        yield self
        for part in self.parts:
            if isinstance(part, Node):
                yield from part.nodes()

    def text(self):
        if self.hidden() or self.tag in {'script', 'style'}:
            return ''
        return ' '.join(' '.join(p.text() if isinstance(p, Node) else p
                                 for p in self.parts).split())

    def ids(self, *ids):
        return [n for n in self.nodes() if n.attrs.get('id') in ids]


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.parts.append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.current.tag == tag:
            self.current = self.current.parent

    def handle_endtag(self, tag):
        node = self.current
        while node.parent:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.parts.append(data)


def unique(values):
    return list(dict.fromkeys(v for v in values if v))


def amounts(text):
    # Dollar symbols bind the amount; never interpret dates, quantities, or percentages.
    return unique(f'{Decimal(m.replace(",", "")):.2f}' for m in
                  re.findall(r'(?<![A-Z])(?:US\s*)?\$\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?)(?![\d.])', text))


def crossed(node, root):
    while node is not root:
        if (node.tag in {'s', 'del'} or 'a-text-price' in node.attrs.get('class', '').split()
                or node.attrs.get('data-a-strike') == 'true'
                or 'line-through' in node.attrs.get('style', '')):
            return True
        node = node.parent
    return False


def parse_offer(root, scope):
    prices, originals = [], []
    price_roots = root.ids('corePriceDisplay_desktop_feature_div', 'corePrice_feature_div',
                           'apex_desktop', 'aod-offer-price') or [root]
    for price_root in price_roots:
        for n in price_root.nodes():
            if 'a-offscreen' not in n.attrs.get('class', '').split():
                continue
            values = amounts(n.text())
            (originals if crossed(n, root) else prices).extend(values)
        # Some Amazon offers omit the accessible text; use complete, same-node
        # symbol/whole/fraction parts only, never guess a missing decimal part.
        for n in price_root.nodes():
            if 'a-price' not in n.attrs.get('class', '').split():
                continue
            children = list(n.nodes())
            if any(amounts(c.text()) for c in children if 'a-offscreen' in c.attrs.get('class', '').split()):
                continue
            parts = {key: unique(c.text() for c in children if key in c.attrs.get('class', '').split())
                     for key in ('a-price-symbol', 'a-price-whole', 'a-price-fraction')}
            if any(len(values) != 1 for values in parts.values()):
                continue
            symbol, whole, fraction = (parts[key][0] for key in parts)
            whole = re.sub(r'\s', '', whole).rstrip('.')
            if symbol not in {'$', 'US$'} or not re.fullmatch(r'[0-9]+(?:,[0-9]{3})*', whole) or not re.fullmatch(r'[0-9]{2}', fraction):
                continue
            (originals if crossed(n, root) else prices).extend(amounts(f'{symbol}{whole}.{fraction}'))
    prices = unique(prices)
    originals = unique(originals)
    price = prices[0] if len(prices) == 1 else None
    # Preserve both displayed and original amounts. The user's price basis uses
    # a unique higher original price from this same offer, without multiplication.
    original = originals[0] if len(originals) == 1 else None
    if price and original and Decimal(original) < Decimal(price):
        original = None
    seller_nodes = root.ids('sellerProfileTriggerId', 'aod-offer-soldBy')
    sellers = []
    for n in seller_nodes:
        links = [c.text() for c in n.nodes() if c.tag == 'a' and c.text()]
        sellers.extend(links or [re.sub(r'^Sold by\s*', '', n.text(), flags=re.I)])
    sellers = unique(sellers)
    # Modern buybox uses a separate "Sold by" section, not the "Ships from" merchant.
    if not sellers:
        for n in root.nodes():
            if n.attrs.get('tabular-attribute-name') == 'Sold by':
                links = [c.text() for c in n.nodes() if c.tag == 'a' and c.text()]
                sellers.extend(links or [re.sub(r'^Sold by\s*', '', n.text(), flags=re.I)])
    if not sellers:
        for n in root.ids('merchant-info'):
            match = re.search(r'(?:Ships from and sold by|Sold by)\s+(.+?)(?:\s+and\s+(?:Fulfilled|shipped)\s+by|\.(?:\s|$)|$)', n.text(), re.I)
            if match:
                sellers.append(match.group(1).rstrip('.'))
    sellers = unique(sellers)
    delivery_nodes = root.ids('mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE',
                              'mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_MEDIUM',
                              'mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_SMALL',
                              'mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE',
                              'deliveryBlockMessage', 'aod-offer-delivery', 'delivery-message')
    delivery_nodes = [n for n in delivery_nodes if not any(
        n is not other and n in list(other.nodes()) for other in delivery_nodes)]
    delivery = ' '.join(unique(n.text() for n in delivery_nodes))
    fees = []
    for n in delivery_nodes:
        for c in n.nodes():
            fee = c.attrs.get('data-csa-c-delivery-price', '').strip()
            if re.fullmatch(r'(?:FREE|\$0(?:\.00)?)', fee, re.I):
                fees.append('0.00')
            else:
                fees.extend(amounts(fee))
        t = n.text()
        if re.search(r'FREE\s+(?:delivery|shipping)\b', t, re.I) and not re.search(
                r'orders?\s+(?:over|of|above|\$)|on\s+\$|with Prime|Prime members?|eligible|qualifying|first order|minimum (?:order|purchase)', t, re.I):
            fees.append('0.00')
        for match in re.finditer(r'\$\s*[0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?\s+(?:delivery|shipping)\b|(?:delivery|shipping)(?:\s+(?:fee|charge))?\s*:?\s*\$\s*[0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?(?![\d.])', t, re.I):
            fees.extend(amounts(match.group()))
    fees = unique(fees)
    conditional = re.search(r'orders?\s+(?:over|of|above|\$)|on\s+\$|with Prime|Prime members?|eligible|qualifying|first order|minimum (?:order|purchase)', delivery, re.I)
    shipping = fees[0] if len(fees) == 1 and not conditional else None
    if re.search(r'\b(?:CAD|AUD|EUR|GBP|JPY)\b|(?:CA|AU|C|A)\$|[€£¥]', root.text(), re.I):
        price = original = shipping = None
    return dict(seller=sellers[0] if len(sellers) == 1 else None, price=price,
                original_price=original, basis_price=original or price, shipping=shipping,
                delivery=delivery or None, currency='USD' if price else None,
                offer_scope=scope, price_text=' | '.join(prices), shipping_text=delivery)


def amazon_source(html, asin):
    if needs_verification(html):
        raise HumanVerificationRequired('Amazon 要求人机验证')
    identity = ProductHTML()
    identity.feed(html)
    if identity.asins != {asin}:
        raise ValueError('网页 ASIN 与当前商品不一致，未保存采集结果')
    doc = Document(html).root
    panels = doc.ids('aod-offer-list')
    entrances = doc.ids('aod-ingress-link', 'buybox-see-all-buying-choices', 'buybox-see-all-buying-choices-announce')
    entrances += [n for n in doc.nodes() if n.tag in {'a', 'button'} and (
        'aod' in n.attrs.get('href', '') or n.attrs.get('data-action') == 'show-all-offers-display'
        or re.search('Other sellers on Amazon|See All Buying Options', n.text(), re.I))]
    result, issues = None, []
    if panels:
        offers = []
        for panel in panels:
            for i, node in enumerate(panel.ids('aod-offer')):
                ancestor = node
                excluded = False
                while ancestor is not panel:
                    if re.search(r'aod-pinned-offer|recommend|sponsored', ancestor.attrs.get('id', ''), re.I):
                        excluded = True
                    ancestor = ancestor.parent
                heading = ' '.join(n.text() for n in node.ids('aod-offer-heading'))
                if excluded or heading.strip().casefold() != 'new' or re.search(r'(?:prime|members?)[ -]*(?:exclusive|only|price)|(?:price|deal|offer)\s*(?:exclusive to|for)\s*(?:prime|members)|join prime to buy|prime members', node.text(), re.I):
                    continue
                ids = {n.attrs.get('value', n.attrs.get('data-asin', '')) for n in node.nodes()
                       if n.attrs.get('name', '').lower() == 'asin' or n.attrs.get('data-asin')}
                if ids and ids != {asin}:
                    raise ValueError('报价行 ASIN 不匹配，未保存采集结果')
                offer = parse_offer(node, f'sellers_on_amazon_list:{i + 1}')
                offers.append(offer)
        if len(offers) > 1:
            issues.append('Sellers on Amazon 存在多条普通新品报价，未擅自选择')
            result = {}
        elif offers:
            result = offers[0]
    elif entrances:
        return {'status': 'browser_required', 'reason': '需展开 Sellers on Amazon 报价列表核对'}
    if result is None:
        roots = doc.ids('catalog-main-offer', 'ppd', 'desktop_buybox', 'buybox')
        # The complete buybox keeps seller, price, delivery in one offer container.
        if roots:
            root = roots[0]
            # Ignore panels/recommendations when locating buybox facts in #ppd.
            for node in list(root.nodes()):
                ident = node.attrs.get('id', '')
                if re.search(r'recommend|sponsored|aod-|all-offers|primeSavings', ident, re.I):
                    node.attrs['hidden'] = ''
        result = parse_offer(roots[0], 'main_offer') if roots else {}
        if roots and re.search(r'(?:prime|members?)[ -]*(?:exclusive|only|price)|join prime to buy|prime members', roots[0].text(), re.I):
            result = {}
            issues.append('尚未切换到可核验的普通报价')
    for key, label in [('seller', '卖家'), ('price', '商品价格'), ('delivery', '配送时间'), ('shipping', '运费')]:
        if not result.get(key):
            issues.append(f'{label}未能明确核验')
    location = ' '.join(unique(n.text() for n in doc.ids('glow-ingress-line2', 'contextualIngressPtLabel_deliveryShortLine')))
    if not re.search(r'\b10001\b', location):
        issues.append('配送地区未核验为 10001；价格及配送信息仅作为当前网页记录')
    return dict(status='partial' if issues else 'captured', asin=asin, **result,
                delivery_location=location or None, issues=issues)


def source_routes(catalog):
    router = APIRouter(prefix='/api/products')

    def target(lid, sku):
        p = catalog.get(lid)
        if not p:
            raise HTTPException(404, '商品不存在')
        v = p if not sku else next((v for v in p['variants'] if v['sku'] == sku), None)
        if v is None:
            raise HTTPException(409, '子 SKU 不属于当前商品')
        if p['source'].casefold() != 'amazon' or not re.fullmatch('[A-Z0-9]{10}', v['asin']):
            raise HTTPException(422, '当前商品没有明确的 Amazon ASIN')
        return v

    def save(lid, sku, asin, result, request, url, method):
        if result['status'] == 'browser_required':
            return result | {'asin': asin, 'source_url': url}
        result.update(source_url=url, captured_at=datetime.now(timezone.utc).isoformat(),
                      variant_sku=sku or '', method=method)
        with catalog.connect() as db:
            write_lock(db)
            row = db.execute('SELECT data_json FROM listings WHERE id=?', (lid,)).fetchone()
            p = json.loads(row[0]) if row else {}
            v = p if not sku else next((v for v in p.get('variants', []) if v['sku'] == sku), {})
            if v.get('asin') != asin or p.get('source', '').casefold() != 'amazon':
                raise HTTPException(409, '抓取期间商品身份发生变化，结果未保存')
            user = getattr(request.state, 'user', None)
            db.execute('INSERT INTO source_captures VALUES(?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex, lid, sku or '', asin, result['captured_at'],
                        user['id'] if user else None, json.dumps(result, ensure_ascii=False)))
        return result

    @router.get('/{lid}/source-capture')
    def current(lid: str, variant_sku: str = ''):
        v = target(lid, variant_sku)
        with catalog.connect() as db:
            row = db.execute('''SELECT data_json FROM source_captures
                WHERE listing_id=? AND variant_sku=? AND asin=? ORDER BY captured_at DESC,id DESC LIMIT 1''',
                (lid, variant_sku, v['asin'])).fetchone()
        return json.loads(row[0]) if row else None

    @router.post('/{lid}/source-capture')
    def capture(lid: str, data: CaptureInput, request: Request):
        v = target(lid, data.variant_sku)
        asin = v['asin']
        url = f'https://www.amazon.com/dp/{asin}?th=1&psc=1'
        try:
            result = amazon_source(fetch_amazon_page(asin), asin)
        except HumanVerificationRequired:
            return {'status': 'verification_required', 'asin': asin, 'source_url': url}
        except ValueError as exc:
            raise HTTPException(502, str(exc)) from None
        if result['status'] == 'partial':
            return {'status': 'browser_required', 'asin': asin, 'source_url': url}
        return save(lid, data.variant_sku, asin, result, request, url, 'server')

    @router.post('/{lid}/source-capture/browser')
    def browser(lid: str, data: BrowserCaptureInput, request: Request):
        v = target(lid, data.variant_sku)
        try:
            url = urlparse(data.source_url)
            if (url.scheme != 'https' or url.netloc not in {'www.amazon.com', 'www.amazon.com:443'}
                    or not re.fullmatch(r'/(?:[^/]+/)?dp/' + re.escape(v['asin']) + r'/?', url.path)):
                raise ValueError('浏览器页面与当前商品 ASIN 不匹配')
            if not Document(data.html).root.ids('catalog-main-offer', 'ppd', 'desktop_buybox', 'buybox', 'aod-offer-list'):
                raise ValueError('浏览器未返回报价区域；请更新验证助手后重新采集')
            result = amazon_source(data.html, v['asin'])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return save(lid, data.variant_sku, v['asin'], result, request, data.source_url, 'browser')

    return router
