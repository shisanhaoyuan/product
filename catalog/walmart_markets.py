"""Market routing and credential isolation for listing preparation and feeds."""
import os

from fastapi import HTTPException

from .credential_vault import decrypt


def market_of(bound):
    return dict(bound or {}).get('marketplace', 'US')


def require_sources(items, marketplace):
    for item in items:
        source_market = 'CA' if item['source'] in ('GigaB2B_CA', 'Amazon_CA') else 'US'
        if source_market != marketplace:
            raise HTTPException(409, '加拿大来源商品仅能预上架到加拿大站 CAD 店铺；美国来源商品仅能预上架到美国站 USD 店铺，不能跨站或自动换汇')


def spec_version(db, store_id):
    from .walmart_publishing import cache_value
    from .walmart_listing_schema import DEFAULT_VERSION
    bound = db.execute('SELECT * FROM walmart_accounts WHERE store_id=?', (store_id,)).fetchone()
    if market_of(bound) == 'CA':
        bundle = cache_value(db, bound, 'ca_bundle') or {}
        return bundle['version']
    return os.environ.get('CATALOG_WALMART_SPEC_VERSION', DEFAULT_VERSION)


def session(publisher, bound, *, writing=False):
    market = market_of(bound)
    credentials = decrypt(bound['store_id'], bound['encrypted_credentials'], marketplace=market)
    options = {'writing': True} if writing else {}
    if market == 'CA':
        options.update(marketplace='CA', specifications=bound.get('_ca_bundle'))
    return publisher.session(credentials, bound['partner_id'], **options)


def header(schema, version):
    return schema['_ca']['header'] if '_ca' in schema else {
        'businessUnit': 'WALMART_US', 'locale': 'en', 'version': version}
