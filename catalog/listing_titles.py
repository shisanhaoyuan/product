"""Deterministic brand prefix for listing copy; never changes source records."""
import html
import re


def source_brands(source):
    values = [source.get('brand')]
    if source.get('source') == 'GigaB2B':
        detail = ((source.get('source_facts') or {}).get('giga') or {}).get('detail') or {}
        values.extend(b.get('brandName') for b in detail.get('brandInfo') or [] if isinstance(b, dict))
    return list(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))


def branded_title(title, brand, brands=()):
    """Remove known brands as whole tokens, then prefix the target exactly once.

    Unknown words are retained: guessing that a title's first word is a brand
    could erase actual product facts. No truncation of dimensions or words.
    """
    if not isinstance(title, str) or not isinstance(brand, str) or not brand.strip():
        return title
    body = html.unescape(re.sub(r'<[^>]*>', ' ', title)).strip()
    brand = brand.strip()
    for known in sorted({brand, *(v.strip() for v in brands if isinstance(v, str) and v.strip())}, key=len, reverse=True):
        pattern = re.escape(known).replace(r'\ ', r'\s+')
        # Letter/digit boundaries protect e.g. Oak in Oakwood and Apple in Pineapple.
        pattern = (r'(?<!\w)' if known[0].isascii() and known[0].isalnum() else '') + pattern
        pattern += r'(?!\w)' if known[-1].isascii() and known[-1].isalnum() else ''
        body = re.sub(pattern + r'[™®]?', '', body, flags=re.I)
    body = re.sub(r'\(\s*\)|\[\s*\]', '', body)
    body = re.sub(r'([-|:;·–—])\s*[-|:;·–—](?=\s|$)', r'\1', body)
    body = re.sub(r'\s+', ' ', body).strip(' \t\r\n-|:;,·–—™®')
    return f'{brand} {body}' if body else ''
