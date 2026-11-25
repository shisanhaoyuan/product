"""Amazon storefront identity, currency and delivery location stay together."""
import re

MARKETS = {
    'US': dict(marketplace='US', source='Amazon', host='www.amazon.com', currency='USD',
               postal_code='10001', namespace='amazon-browser-us'),
    'CA': dict(marketplace='CA', source='Amazon_CA', host='www.amazon.ca', currency='CAD',
               postal_code='M5V 3L9', namespace='amazon-browser-ca'),
}


def market_config(marketplace='US'):
    if marketplace not in MARKETS:
        raise ValueError('不支持的亚马逊站点')
    return MARKETS[marketplace]


def source_market(source):
    return 'CA' if source == 'Amazon_CA' else 'US'


def product_url(asin, marketplace='US'):
    return f'https://{market_config(marketplace)["host"]}/dp/{asin}?th=1&psc=1'


def location_matches(text, marketplace='US'):
    market_config(marketplace)
    pattern = r'\bM5V\s*3L9\b' if marketplace == 'CA' else r'\b10001\b'
    return bool(re.search(pattern, text or '', re.I))
