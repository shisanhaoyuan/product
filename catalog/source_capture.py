"""ASIN-bound source observations, stored separately from ERP prices and evidence."""
from .amazon_markets import market_config, source_market, location_matches, product_url
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


class SourceCaptureInput(CaptureInput):
    background: bool = False


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


def amounts(text, marketplace="US"):
    # Dollar symbols bind the amount; never interpret dates, quantities, or percentages.
    if marketplace == 'CA':
        text = re.sub(r'\b(?:CAD|CDN|CA|C)\s*\$', '$', text, flags=re.I)
    return unique(f'{Decimal(m.replace(",", "")):.2f}' for m in
                  re.findall(r'(?<![A-Z])(?:US\s*)?\$\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?)(?![\d.])', text))


def unit_price(node, root):
    """Per-count/weight amounts are neither the item price nor its reference price."""
    while node is not None:
        markers = (node.attrs.get('class', '') + ' ' + node.attrs.get('id', '')).lower()
        if any(marker in markers for marker in ('contains-ppu', 'priceperunit', 'price-per-unit')):
            return True
        if node is root:
            break
        node = node.parent
    return False


def crossed(node, root, explicit=False):
    while node is not root:
        if (node.tag in {'s', 'del', 'strike'}
                or (not explicit and 'a-text-price' in node.attrs.get('class', '').split()
                    and node.attrs.get('data-a-strike') != 'false')
                or node.attrs.get('data-a-strike') == 'true'
                or 'line-through' in node.attrs.get('style', '')):
            return True
        node = node.parent
    return False


def parse_offer(root, scope, marketplace="US"):
    prices, originals, struck_originals = [], [], []

    def record(node, values):
        if unit_price(node, root):
            return
        if crossed(node, root, explicit=True):
            struck_originals.extend(values)
        elif crossed(node, root):
            originals.extend(values)
        else:
            prices.extend(values)

    for node in list(root.nodes()):
        classes = node.attrs.get('class', '').split()
        if 'a-accordion-row' in classes and 'a-accordion-active' not in classes:
            node.attrs['hidden'] = ''
    price_roots = root.ids('corePriceDisplay_desktop_feature_div', 'corePrice_feature_div',
                           'apex_desktop', 'apex_mobile', 'corePrice_desktop', 'corePrice_mobile_feature_div', 'aod-offer-price') or [root]
    for price_root in price_roots:
        for n in price_root.nodes():
            if not ({'a-offscreen','apex-pricetopay-accessibility-label','apex-basisprice-offscreen-label'} & set(n.attrs.get('class', '').split()) or n.attrs.get('id')=='apex-pricetopay-accessibility-label'):
                continue
            values = amounts(n.text(), marketplace)
            if 'apex-basisprice-offscreen-label' in n.attrs.get('class','').split():
                originals.extend(values)
            else:
                record(n, values)
        # Some Amazon offers omit the accessible text; use complete, same-node
        # symbol/whole/fraction parts only, never guess a missing decimal part.
        for n in price_root.nodes():
            if 'a-price' not in n.attrs.get('class', '').split():
                continue
            children = list(n.nodes())
            if any(amounts(c.text(), marketplace) for c in children if 'a-offscreen' in c.attrs.get('class', '').split()):
                continue
            parts = {key: unique(c.text() for c in children if key in c.attrs.get('class', '').split())
                     for key in ('a-price-symbol', 'a-price-whole', 'a-price-fraction')}
            if any(len(values) != 1 for values in parts.values()):
                continue
            symbol, whole, fraction = (parts[key][0] for key in parts)
            whole = re.sub(r'\s', '', whole).rstrip('.')
            if symbol not in ({'$', 'C$', 'CA$', 'CDN$', 'CAD$'} if marketplace=='CA' else {'$', 'US$'}) or not re.fullmatch(r'[0-9]+(?:,[0-9]{3})*', whole) or not re.fullmatch(r'[0-9]{2}', fraction):
                continue
            record(n, amounts(f'{symbol}{whole}.{fraction}', marketplace))
    # Legacy buyboxes expose visible dollar amounts without a-offscreen markup.
    for node in root.ids('price_inside_buybox', 'newBuyBoxPrice', 'priceblock_ourprice', 'priceblock_dealprice'):
        record(node, amounts(node.text(), marketplace))
    prices = unique(prices)
    # Explicit strikethrough wins over legacy styling shared by other amounts.
    originals = unique(struck_originals or originals)
    price = prices[0] if len(prices) == 1 else None
    # Preserve both displayed and original amounts. The user's price basis uses
    # a unique higher original price from this same offer, without multiplication.
    eligible_originals = [value for value in originals
                          if not price or Decimal(value) > Decimal(price)]
    original = eligible_originals[0] if len(eligible_originals) == 1 else None
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
                              'deliveryBlockMessage', 'deliveryMessageMirId', 'aod-offer-delivery', 'delivery-message')
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
                fees.extend(amounts(fee, marketplace))
        t = n.text()
        if re.search(r'FREE\s+(?:delivery|shipping)\b', t, re.I) and not re.search(
                r'orders?\s+(?:over|of|above|\$)|on\s+\$|with Prime|Prime members?|eligible|qualifying|first order|minimum (?:order|purchase)', t, re.I):
            fees.append('0.00')
        for match in re.finditer(r'\$\s*[0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?\s+(?:delivery|shipping)\b|(?:delivery|shipping)(?:\s+(?:fee|charge))?\s*:?\s*\$\s*[0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?(?![\d.])', t, re.I):
            fees.extend(amounts(match.group(), marketplace))
    fees = unique(fees)
    conditional = re.search(r'orders?\s+(?:over|of|above|\$)|on\s+\$|with Prime|Prime members?|eligible|qualifying|first order|minimum (?:order|purchase)', delivery, re.I)
    shipping = fees[0] if len(fees) == 1 and not conditional else None
    foreign = r'\b(?:USD|AUD|EUR|GBP|JPY)\b|\b(?:US|AU|A)\$|[€£¥]' if marketplace == 'CA' else r'\b(?:CAD|AUD|EUR|GBP|JPY)\b|(?:CA|AU|C|A)\$|[€£¥]'
    if re.search(foreign, root.text(), re.I):
        price = original = shipping = None
    return dict(seller=sellers[0] if len(sellers) == 1 else None, price=price,
                original_price=original, basis_price=original or price, shipping=shipping,
                original_price_state=('verified' if original else 'ambiguous' if originals else 'not_displayed'),
                delivery=delivery or None, currency=market_config(marketplace)['currency'] if price or original else None,
                offer_scope=scope, price_text=' | '.join(prices), shipping_text=delivery)


def amazon_source(html, asin, marketplace="US"):
    if needs_verification(html):
        raise HumanVerificationRequired('Amazon 要求人机验证')
    identity = ProductHTML()
    identity.feed(html)
    if identity.asins != {asin}:
        raise ValueError('网页 ASIN 与当前商品不一致，未保存采集结果')
    doc = Document(html).root
    incomplete = any(n.attrs.get('data-offers-incomplete') == 'true' for n in doc.nodes())
    panels = doc.ids('aod-offer-list')
    entrances = doc.ids('aod-ingress-link', 'buybox-see-all-buying-choices', 'buybox-see-all-buying-choices-announce')
    entrances += [n for n in doc.nodes() if n.tag in {'a', 'button'} and (
        'aod' in n.attrs.get('href', '') or n.attrs.get('data-action') == 'show-all-offers-display'
        or re.search('Other sellers on Amazon|See All Buying Options', n.text(), re.I))]
    issues = []
    def main_offer():
        roots = Document(html).root.ids('catalog-main-offer', 'ppd', 'desktop_buybox', 'buybox', 'buybox_feature_div', 'mobile_buybox')
        # The complete buybox keeps seller, price, delivery in one offer container.
        if roots:
            root = roots[0]
            # Ignore panels/recommendations when locating buybox facts in #ppd.
            for node in list(root.nodes()):
                ident = node.attrs.get('id', '')
                if re.search(r'recommend|sponsored|aod-|all-offers|primeSavings', ident, re.I):
                    node.attrs['hidden'] = ''
        if roots:
            ids = {n.attrs.get('value', '') for n in roots[0].nodes()
                   if n.attrs.get('name', '').lower() in ('asin', 'items[0.base][asin]')}
            if ids and ids != {asin}:
                raise ValueError('主报价 ASIN 不匹配，未保存采集结果')
        result = parse_offer(roots[0], 'main_offer', marketplace) if roots else {}
        if roots and re.search(r'(?:prime|members?)[ -]*(?:exclusive|only|price)|join prime to buy|prime members', roots[0].text(), re.I):
            result = {}
            issues.append('尚未切换到可核验的普通报价')
        return result
    primary = main_offer()
    # The visible main offer is the selected product's quote. Other sellers
    # must not erase it just because their drawer has multiple/incomplete rows.
    main_usable = bool(primary.get('original_price') or primary.get('price'))
    if incomplete and not main_usable:
        return dict(status='partial', asin=asin, price=None, original_price=None, basis_price=None,
                    delivery_location=' '.join(n.text() for n in doc.ids('glow-ingress-line2', 'contextualIngressPtLabel_deliveryShortLine')),
                    issues=['卖家报价列表尚未加载完整，主报价也未取得价格'])
    result = primary if main_usable else None
    if panels and not main_usable:
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
                       if n.attrs.get('name', '').lower() in ('asin', 'items[0.base][asin]') or n.attrs.get('data-asin')}
                if ids and ids != {asin}:
                    raise ValueError('报价行 ASIN 不匹配，未保存采集结果')
                offer = parse_offer(node, f'sellers_on_amazon_list:{i + 1}', marketplace)
                offers.append(offer)
        if len(offers) > 1:
            issues.append('Sellers on Amazon 存在多条普通新品报价，未擅自选择')
            result = {}
        elif offers:
            result = offers[0]
    elif entrances and not main_usable:
        return {'status': 'browser_required', 'reason': '需展开 Sellers on Amazon 报价列表核对'}
    if result is None:
        result = primary
    for key, label in [('seller', '卖家'), ('price', '商品价格'), ('delivery', '配送时间'), ('shipping', '运费')]:
        if not result.get(key):
            issues.append(('当前售价读取到多个金额：' + result['price_text'] + '；需区分普通价与会员价')
                          if key == 'price' and ' | ' in result.get('price_text', '')
                          else f'{label}未能明确核验')
    location = ' '.join(unique(n.text() for n in doc.ids('glow-ingress-line2', 'contextualIngressPtLabel_deliveryShortLine')))
    if not location_matches(location, marketplace):
        issues.append(f'配送地区未核验为 {market_config(marketplace)["postal_code"]}；价格及配送信息仅作为当前网页记录')
    diagnostics = [dict(state=n.attrs.get('data-collector-offer-state'), nodes=n.attrs.get('data-collector-offer-nodes'))
                   for n in doc.ids('catalog-main-offer') if n.attrs.get('data-collector-offer-state')]
    if diagnostics and diagnostics[0]['state']=='price_missing_after_wait' and not result.get('price') and not result.get('original_price'):
        issues.append('报价区域等待加载后仍未取得价格；商品资料保留入库，可稍后补采')
    return dict(status='partial' if issues else 'captured', asin=asin, **result,
                delivery_location=location or None, issues=issues, offer_diagnostics=diagnostics)



def collect_quote_page(asin, fetch, marketplace="US"):
    """One HTTP attempt, then at most one configured background browser attempt."""
    from .amazon_browser import configured, fetch_page
    from .amazon_pricing import regular_price_basis
    trigger = 'dynamic_offer'
    try:
        html=fetch(asin) if marketplace == "US" else fetch(asin, marketplace)
        quote=amazon_source(html,asin,marketplace)
    except HumanVerificationRequired:
        if not configured():
            raise
        trigger = 'http_verification'
    else:
        if not configured() or (quote.get('status') != 'browser_required'
                                and (quote.get('original_price') or (quote.get('price') and regular_price_basis(quote)))
                                and location_matches(quote.get('delivery_location'),marketplace)):
            return html,quote,'server'
    html=fetch_page(asin) if marketplace == "US" else fetch_page(asin,marketplace)
    quote=amazon_source(html,asin,marketplace)
    if report := getattr(html, 'wraith_test', None):
        quote['wraith_test'] = report | {'trigger': trigger}
    return html,quote,'wraith_background'


def save_capture(catalog, db, lid, sku, asin, result, actor_id, url, method, *, observed_at=None, sync_to_catalog=True):
    """Persist with the caller's transaction so generation can atomically rebase its snapshot."""
    capture_id = uuid.uuid4().hex
    result = dict(result, capture_id=capture_id, source_url=url, captured_at=observed_at or datetime.now(timezone.utc).isoformat(),
                  variant_sku=sku or '', method=method)
    row = db.execute('SELECT data_json FROM listings WHERE id=?', (lid,)).fetchone()
    product = json.loads(row[0]) if row else {}
    target = product if not sku else next((v for v in product.get('variants', []) if v['sku'] == sku), {})
    if target.get('asin') != asin or product.get('source', '').casefold() not in ('amazon','amazon_ca'):
        raise HTTPException(409, '抓取期间商品身份发生变化，结果未保存')
    result['source_digest_before'] = db.execute('SELECT digest FROM current_listings WHERE id=?',(lid,)).fetchone()[0]
    db.execute('INSERT INTO source_captures VALUES(?,?,?,?,?,?,?)',
               (capture_id, lid, sku or '', asin, result['captured_at'], actor_id, json.dumps(result, ensure_ascii=False)))
    if sync_to_catalog and result['status'] != 'verification_required':
        from .source_sync import sync_capture
        result['sync'] = sync_capture(catalog, db, lid, sku, asin, result, actor_id)
    result['source_digest_after'] = db.execute('SELECT digest FROM current_listings WHERE id=?',(lid,)).fetchone()[0]
    db.execute('UPDATE source_captures SET data_json=? WHERE id=?',(json.dumps(result,ensure_ascii=False),capture_id))
    return result


def source_routes(catalog):
    router = APIRouter(prefix='/api/products')

    def target(lid, sku):
        p = catalog.get(lid)
        if not p:
            raise HTTPException(404, '商品不存在')
        v = p if not sku else next((v for v in p['variants'] if v['sku'] == sku), None)
        if v is None:
            raise HTTPException(409, '子 SKU 不属于当前商品')
        if p['source'].casefold() not in ('amazon','amazon_ca') or not re.fullmatch('[A-Z0-9]{10}', v['asin']):
            raise HTTPException(422, '当前商品没有明确的 Amazon ASIN')
        return dict(v, _marketplace=source_market(p['source']))

    def save(lid, sku, asin, result, request, url, method):
        if result['status'] == 'browser_required' and not (result.get('image_collection') or {}).get('images'):
            return result | {'asin': asin, 'source_url': url, 'method': method}
        with catalog.connect() as db:
            write_lock(db)
            user = getattr(request.state, 'user', None)
            return save_capture(catalog, db, lid, sku, asin, result, user['id'] if user else None, url, method)

    @router.get('/{lid}/source-capture/context')
    def context(lid: str):
        product=catalog.get(lid)
        if not product or product['source'] not in ('Amazon','Amazon_CA'):
            raise HTTPException(404,'亚马逊商品不存在')
        return market_config(source_market(product['source']))

    @router.get('/{lid}/source-capture')
    def current(lid: str, variant_sku: str = ''):
        v = target(lid, variant_sku)
        with catalog.connect() as db:
            row = db.execute('''SELECT data_json FROM source_captures
                WHERE listing_id=? AND variant_sku=? AND asin=? ORDER BY captured_at DESC,id DESC LIMIT 1''',
                (lid, variant_sku, v['asin'])).fetchone()
        return json.loads(row[0]) if row else None

    @router.post('/{lid}/source-capture')
    def capture(lid: str, data: SourceCaptureInput, request: Request):
        v = target(lid, data.variant_sku)
        asin = v['asin']
        marketplace=v['_marketplace']
        url = product_url(asin,marketplace)
        try:
            html, result, method = collect_quote_page(asin, fetch_amazon_page) if marketplace=="US" else collect_quote_page(asin, fetch_amazon_page, marketplace)
            from .listing_source_evidence import amazon_details
            result['listing_facts'] = amazon_details(html, asin)
        except HumanVerificationRequired as exc:
            from .amazon_browser import configured
            reason = str(exc) if configured() else '普通请求遇到人机验证，本服务未启用 Wraith'
            result = {'status': 'verification_required', 'asin': asin, 'source_url': url, 'issues': [reason], 'wraith_test': getattr(exc, 'wraith_test', None)}
            return save(lid, data.variant_sku, asin, result, request, url, 'wraith_background') if result['wraith_test'] else result
        except ValueError as exc:
            raise HTTPException(502, str(exc)) from None
        from .source_images import collect_amazon_images
        result['image_collection'] = collect_amazon_images(catalog, lid, data.variant_sku, asin, html)
        if result['status'] == 'partial' and not data.background:
            result['status'] = 'browser_required'
        return save(lid, data.variant_sku, asin, result, request, url, method)

    @router.post('/{lid}/source-capture/sync')
    def sync_existing(lid: str, data: CaptureInput, request: Request):
        v = target(lid, data.variant_sku)
        with catalog.connect() as db:
            write_lock(db)
            row = db.execute('SELECT data_json FROM listings WHERE id=?', (lid,)).fetchone()
            p = json.loads(row[0]) if row else {}
            current = p if not data.variant_sku else next(
                (child for child in p.get('variants', []) if child['sku'] == data.variant_sku), {})
            if current.get('asin') != v['asin'] or p.get('source', '').casefold() not in ('amazon','amazon_ca'):
                raise HTTPException(409, '商品身份已变化，请刷新后重新采集')
            row = db.execute('''SELECT data_json FROM source_captures
                WHERE listing_id=? AND variant_sku=? AND asin=? ORDER BY captured_at DESC,id DESC LIMIT 1''',
                (lid, data.variant_sku or '', v['asin'])).fetchone()
            if not row:
                raise HTTPException(404, '没有可补齐的采集记录，请先采集来源信息')
            result = json.loads(row[0])
            from .source_sync import sync_capture
            user = getattr(request.state, 'user', None)
            result['sync'] = sync_capture(catalog, db, lid, data.variant_sku, v['asin'], result,
                                          user['id'] if user else None)
        return result

    @router.post('/{lid}/source-capture/browser')
    def browser(lid: str, data: BrowserCaptureInput, request: Request):
        v = target(lid, data.variant_sku)
        try:
            url = urlparse(data.source_url)
            if (url.scheme != 'https' or url.netloc not in {market_config(v['_marketplace'])['host'], market_config(v['_marketplace'])['host']+':443'}
                    or not re.fullmatch(r'/(?:[^/]+/)?dp/' + re.escape(v['asin']) + r'/?', url.path)):
                raise ValueError('浏览器页面与当前商品 ASIN 不匹配')
            if not Document(data.html).root.ids('catalog-main-offer', 'ppd', 'desktop_buybox', 'buybox', 'aod-offer-list'):
                raise ValueError('浏览器未返回报价区域；请更新集物采集插件后重新采集')
            result = amazon_source(data.html, v['asin'],v['_marketplace'])
            from .listing_source_evidence import amazon_details
            result['listing_facts'] = amazon_details(data.html, v['asin'])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        from .source_images import collect_amazon_images
        result['image_collection'] = collect_amazon_images(catalog, lid, data.variant_sku, v['asin'], data.html)
        return save(lid, data.variant_sku, v['asin'], result, request, data.source_url, 'browser')

    return router
