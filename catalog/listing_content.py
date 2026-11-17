"""SKU-local listing copy/layout and image limits, independent of ERP automation.

Description policy follows digit-emperor-listing / description_format.py, with
an explicit exception: independent Key Features have priority over description.
"""
import html
import re
import unicodedata
from html.parser import HTMLParser

MAX_IMAGES = 8
HEADINGS = r'Features?|Key Features|Product Information|Specifications?|Product Description'
FORBIDDEN = re.compile(
    r'warrant(?:y|ies)|manufacturer|\b(?:Amazon|ASIN|SKU|UPC(?:[- ]?[AE])?|EAN(?:[- ]?(?:8|13))?|'
    r'GTIN(?:[- ]?(?:8|12|13|14))?|MPN|model name|model number|seller|sold by|merchant|'
    r'price|discount|shipping|delivery|money[- ]back guarantee|satisfaction guarantee|best sellers? rank|customer reviews?|Universal Product Code|European Article Number|'
    r'International Article Number|Global Trade (?:Item|Identification) Number)\b|保修|制造商', re.I)


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        style = re.sub(r'\s+', '', attrs.get('style', '')).lower()
        hidden = bool(self.hidden and self.hidden[-1]) or tag in ('script', 'style', 'template') or 'hidden' in attrs or attrs.get('aria-hidden') == 'true' or 'display:none' in style or 'visibility:hidden' in style
        if tag not in ('br', 'img', 'hr', 'input', 'meta', 'link', 'source', 'wbr'):
            self.hidden.append(hidden)
        if not hidden and tag in ('p', 'br', 'li', 'div', 'tr', 'h1', 'h2', 'h3', 'h4'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        hidden = bool(self.hidden and self.hidden[-1])
        if self.hidden:
            self.hidden.pop()
        if not hidden and tag in ('p', 'li', 'div', 'tr', 'h1', 'h2', 'h3', 'h4'):
            self.parts.append('\n')
        if tag in ('td', 'th'):
            self.parts.append(' ')

    def handle_data(self, value):
        if not self.hidden or not self.hidden[-1]:
            self.parts.append(value)


def text(value):
    parser = Text()
    parser.feed(str(value or ''))
    return re.sub(r'\n+', '\n', ''.join(parser.parts).replace('\xa0', ' ')).strip()


def key(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', text(value)).casefold() if c.isalnum())


def sentences(value):
    value = text(value)
    protected = {}

    def keep(match):
        token = f'\ue000{len(protected)}\ue001'
        protected[token] = match[0]
        return token

    value = re.sub(r'(?i)\b(?:e\.g\.|i\.e\.|U\.S\.(?:A\.)?|Mr\.|Mrs\.|Dr\.|No\.|vs\.|approx\.|in\.|ft\.|oz\.|lb\.|lbs\.|cm\.|mm\.)|\d+\.\d+|https?://\S+|\b\w+\.(?:com|net|org)\b', keep, value)
    # Explicit numbering, including legacy "1.Point"; never a decimal.
    value = re.sub(r'(?m)(?:^|(?<=\s))\d{1,2}[.)](?!\d)\s*(?=[A-Z\u4e00-\u9fff])', '\n', value)
    value = re.sub(r'(?i)(?:^|\n)\s*(?:'+HEADINGS+r')\s*:\s*', '\n', value)
    parts = re.split(r'\n+|(?<=[。！？!?])\s*|(?<=\.)(?=\s|[A-Z])\s*', value)
    result = []
    for part in parts:
        for token, original in protected.items():
            part = part.replace(token, original)
        part = re.sub(r'\s+', ' ', part).strip(' \t•')
        if part and key(part):
            result.append(part)
    return result


def fingerprints(part):
    result = {key(part)}
    if ':' in part:
        result.add(key(part.split(':', 1)[1]))
    return result - {''}


def copy_policy(visible):
    original = visible.get('keyFeatures')
    features, seen = [], set()
    for feature in original if isinstance(original, list) else []:
        parts = []
        for part in sentences(feature):
            alternatives = fingerprints(part)
            if not seen.intersection(alternatives):
                seen.update(alternatives)
                parts.append(part)
        if parts:
            features.append(' '.join(parts))
    if isinstance(original, list) and features != original:
        visible['keyFeatures'] = features
    for field in ('shortDescription', 'longDescription'):
        description_policy(visible, field, seen)


def description_policy(visible, field, seen):
    description = visible.get(field, '')
    raw = re.sub(r'(?im)^\s*#{1,6}\s*', '', text(description))
    raw = re.sub(r'\*\*([^*]+)\*\*', r'\1', raw)
    raw = re.sub(r'(?i)(?<!\w)('+HEADINGS+r')\s*:', r'\n\1:\n', raw)
    sections = {'Features': [], 'Product Information': []}
    heading = 'Features'
    for line in raw.splitlines():
        match = re.fullmatch(r'\s*('+HEADINGS+r')\s*:\s*', line, re.I)
        if match:
            heading = 'Product Information' if re.search(r'information|specification', match[1], re.I) else 'Features'
            continue
        # Structured specifications may be separated by semicolons, not arbitrary commas.
        for part in sentences(line.replace('; ', '\n') if heading == 'Product Information' else line):
            alternatives = fingerprints(part)
            if FORBIDDEN.search(part) or seen.intersection(alternatives):
                continue
            seen.update(alternatives)
            sections[heading].append(part)
    points = [p for values in sections.values() for p in values]
    if not points:
        if description:
            visible[field] = ''
        return
    # Retain a single already readable line; numbered sections are used for multiple points.
    if len(points) == 1 and points[0] == text(description) and '\n' not in description:
        return
    visible[field] = '\n'.join(
        '<p><strong>'+title+':</strong></p>\n' + '\n'.join(
            f'<p>{i}. {html.escape(point)}</p>' for i, point in enumerate(values, 1))
        for title, values in sections.items() if values)


def source_specifications(visible, facts):
    """Append only verified same-SKU specifications, never generated estimates."""
    attributes = facts.get('content.attributes') or facts.get('detail.attributes') or {}
    attributes = dict(attributes) if isinstance(attributes, dict) else {}
    for field, label in (('mainColor', 'Color'), ('mainMaterial', 'Material'), ('placeOfOrigin', 'Place of Origin')):
        value = facts.get('detail.'+field)
        if value not in (None, ''):
            attributes.setdefault(label, value)
    for field, label in (('assembledLength', 'Assembled Length'), ('assembledWidth', 'Assembled Width'),
                         ('assembledHeight', 'Assembled Height'), ('assembledWeight', 'Assembled Weight')):
        value = facts.get('detail.'+field)
        unit = facts.get('detail.assembledWeightUnit' if field.endswith('Weight') else 'detail.assembledLengthUnit')
        if value not in (None, '') and unit:
            attributes.setdefault(label, f'{value} {unit}')
    description = visible.get('shortDescription', '')
    existing = {key(p) for p in sentences(description)}
    additions = []
    for label, value in attributes.items():
        if not isinstance(value, (str, int, float)) or isinstance(value, bool) or not str(value).strip():
            continue
        point = f'{label}: {value}'
        if not FORBIDDEN.search(point) and key(point) not in existing:
            additions.append(point)
    if additions:
        visible['shortDescription'] = description + '<p><strong>Product Information:</strong></p>' + ''.join(
            '<p>'+html.escape(p)+'</p>' for p in additions)


def image_urls(visible):
    secondary = visible.get('productSecondaryImageURL', [])
    return list(dict.fromkeys(u.strip() for u in [visible.get('mainImageUrl', ''), *(secondary if isinstance(secondary, list) else [])]
                              if isinstance(u, str) and u.strip()))


def image_policy(visible):
    urls = image_urls(visible)[:MAX_IMAGES]
    if urls:
        visible['mainImageUrl'] = urls[0]
    if 'productSecondaryImageURL' in visible or len(urls) > 1:
        visible['productSecondaryImageURL'] = urls[1:]


def normalize(visible):
    copy_policy(visible)
    image_policy(visible)
