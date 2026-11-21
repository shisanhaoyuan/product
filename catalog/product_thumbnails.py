"""Read-only product-library covers, using already stored images when available."""
import hashlib
from collections import defaultdict

from .database import is_postgres
from .source_images import PREFIX


def page_image_evidence(db, listing_ids):
    by_listing = defaultdict(list)
    if not listing_ids:
        return by_listing
    marks = ','.join('?' for _ in listing_ids)
    stored = 'EXISTS (SELECT 1 FROM image_objects o WHERE o.digest=e.digest)'
    if not is_postgres(db):
        stored += ' OR EXISTS (SELECT 1 FROM image_assets a WHERE a.digest=e.digest)'
    for row in db.execute(f'''SELECT e.listing_id,e.source_sku,e.url_hash,e.digest
            FROM listing_image_evidence e
            WHERE e.listing_id IN ({marks}) AND ({stored})
            ORDER BY e.created_at DESC,e.digest''', listing_ids):
        by_listing[row['listing_id']].append(row)
    return by_listing


def thumbnail_urls(product, evidence):
    # Manual edits own their gallery, including an explicitly emptied gallery.
    edited = {c.get('variant_sku', '') for c in product['local_edit']['changes']
              if c['field'] == 'images' and not c.get('capture_asin')}
    scopes = [('', product['images'])]
    if '' not in edited:
        scopes.extend((v['sku'], v['images']) for v in product['variants'])
    remote = list(dict.fromkeys(url for _, images in scopes for url in images))
    local = [url for url in remote if url.startswith(PREFIX)]
    hashes = {hashlib.sha256(url.encode()).hexdigest(): url for url in remote} if evidence else {}
    # Parent collection uses either the parent SKU or the empty SKU.
    scopes_by_sku = dict(scopes)
    scopes_by_sku.setdefault(product['sku'], product['images'])
    matches = defaultdict(list)
    recovered = []
    for row in evidence:
        sku = row['source_sku']
        if '' not in edited and sku not in scopes_by_sku:
            continue
        url = hashes.get(row['url_hash'])
        asset = PREFIX + row['digest']
        if url:
            matches[url].append(asset)
        elif '' not in edited and sku not in edited:
            recovered.append(asset)

    # Keep an explicitly chosen parent cover first, using its exact cached copy if possible.
    preferred = []
    if '' in edited and remote:
        preferred = [*matches[remote[0]], remote[0]]
    cached = [asset for url in remote for asset in matches.get(url, ())]
    # Include a representative variant photo early even if the whole parent CDN gallery fails.
    child_covers = [images[0] for sku, images in scopes if sku and images]
    stored = list(dict.fromkeys(local + cached + recovered))[:6]
    candidates = preferred + stored + remote[:1] + child_covers[:2] + remote
    return list(dict.fromkeys(candidates))[:12]
