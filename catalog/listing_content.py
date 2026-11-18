"""SKU-local listing copy/layout and image normalization, independent of ERP automation.

Description policy follows digit-emperor-listing / description_format.py, with
an explicit exception: independent Key Features have priority over description.
"""
import html
import re
import unicodedata
from html.parser import HTMLParser

DEFAULT_IMAGE_COUNT = 8
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
            self.parts.append('\t')

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
    value = strip_numbering(text(value))
    protected = {}

    def keep(match):
        token = f'\ue000{len(protected)}\ue001'
        protected[token] = match[0]
        return token

    value = re.sub(r'(?i)\b(?:e\.g\.|i\.e\.|U\.S\.(?:A\.)?|Mr\.|Mrs\.|Dr\.|No\.|vs\.|approx\.|in\.|ft\.|oz\.|lb\.|lbs\.|cm\.|mm\.)|\d+\.\d+|https?://\S+|\b\w+\.(?:com|net|org)\b', keep, value)
    # Inline point markers, including lowercase prose. Decimals stay protected.
    value = re.sub(r'(?<=\s)\d+[.)](?!\d)\s*(?=\S)', '\n', value)
    value = re.sub(r'(?i)(?:^|\n)\s*(?:'+HEADINGS+r')\s*:\s*', '\n', value)
    parts = re.split(r'\n+|(?<=[。！？!?])\s*|(?<=\.)(?=\s|[A-Z])\s*', value)
    result = []
    for part in parts:
        for token, original in protected.items():
            part = part.replace(token, original)
        part = re.sub(r'\s+', ' ', part).strip(' \t•')
        part = strip_numbering(part)
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


def strip_numbering(value):
    # Strip only a line's explicit list prefixes. A decimal such as 78.19 is a value.
    value = re.sub(r'(?m)^\s*[-*•]\s+', '', value.replace('\\.', '.'))
    previous = None
    while previous != value:
        previous = value
        value = re.sub(r'(?m)^\s*\d+[.)](?!\d)[ \t]*', '', value)
    return value.strip()


PARAMETER_LABELS = {
    'product type', 'size', 'color', 'main color', 'material', 'material (main)',
    'main material', 'overall product dimension', 'overall product dimensions',
    'product dimensions', 'package number', 'package contents', 'number of packages',
    'weight', 'capacity', 'style', 'use case', 'place of origin', 'bed size',
    'bed frame material', 'bed type', 'box spring requirements', 'wood tone',
}


def description_sections(value):
    raw = re.sub(r'(?im)^\s*#{1,6}\s*', '', text(value))
    raw = re.sub(r'\*\*([^*]+)\*\*', r'\1', raw)
    raw = re.sub(r'(?i)(?<!\w)('+HEADINGS+r')\s*:', r'\n\1:\n', raw)
    lines = [strip_numbering(line) for line in raw.splitlines()]
    lines = [line for line in lines if line]
    sections = {'Features': [], 'Product Information': []}
    heading = 'Features'
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        match = re.fullmatch(r'\s*('+HEADINGS+r')\s*:?\s*', line, re.I)
        if match:
            heading = 'Product Information' if re.search(r'information|specification', match[1], re.I) else 'Features'
            continue
        # HTML table cells carry an explicit boundary; never pair unrelated free text.
        cells = [cell.strip() for cell in line.split('\t') if cell.strip()]
        if len(cells) == 2:
            sections['Product Information'].append(cells[0].rstrip(':')+': '+cells[1])
            continue
        label = line.rstrip(':').strip().casefold()
        if label in PARAMETER_LABELS and index < len(lines):
            following = lines[index]
            if (following.rstrip(':').casefold() not in PARAMETER_LABELS
                    and not re.fullmatch(HEADINGS+r'\s*:', following, re.I)):
                sections['Product Information'].append(line.rstrip(':')+': '+following)
                index += 1
                continue
        for part in sentences(line.replace('; ', '\n') if heading == 'Product Information' else line):
            if not re.fullmatch(HEADINGS+r'\s*:?\s*', part, re.I):
                sections[heading].append(part)
    return sections


def render_sections(sections, seen):
    output = []
    for title, values in sections.items():
        kept = []
        for part in values:
            alternatives = fingerprints(part)
            if not alternatives or FORBIDDEN.search(part) or seen.intersection(alternatives):
                continue
            seen.update(alternatives)
            kept.append(part)
        if kept:
            output.append('<p><strong>'+title+':</strong></p>\n' + '\n'.join(
                f'<p>{i}. {html.escape(point)}</p>' for i, point in enumerate(kept, 1)))
    return '\n'.join(output)


def description_policy(visible, field, seen):
    description = visible.get(field, '')
    sections = description_sections(description)
    points = [p for values in sections.values() for p in values]
    rendered = render_sections(sections, seen)
    if not rendered:
        if description:
            visible[field] = ''
    elif len(points) == 1 and points[0] == text(description) and '\n' not in description:
        return
    else:
        visible[field] = rendered


def source_specifications(visible, facts):
    """Rebuild from this SKU's source snapshot, never append to generated output."""
    source_copy = facts.get('content.copy') or {}
    source_description = source_copy.get('description') or facts.get('detail.description')
    # Some imports have only attributes. Keep existing prose but rebuild its specs.
    sections = description_sections(source_description if source_description is not None else visible.get('shortDescription', ''))
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
    specifications = {}
    unkeyed = []
    for part in sections['Product Information']:
        if ':' in part:
            specifications[key(part.split(':', 1)[0])] = part
        else:
            unkeyed.append(part)
    for label, value in attributes.items():
        if not isinstance(value, (str, int, float)) or isinstance(value, bool) or not str(value).strip():
            continue
        point = f'{label}: {value}'
        if not FORBIDDEN.search(point):
            specifications[key(label)] = point
    sections['Product Information'] = unkeyed + list(specifications.values())
    # Replace source brands after rebuilding, not before: the snapshot is immutable.
    from .listing_templates import replace_brand
    brands = facts.get('source_brands', []) + [facts.get('source_brand', '')]
    for title, values in sections.items():
        sections[title] = [replace_brand(p, brands, visible.get('brand', '')) for p in values]
    seen = set()
    for feature in visible.get('keyFeatures', []):
        for part in sentences(feature):
            seen.update(fingerprints(part))
    description = render_sections(sections, seen)
    if description or 'shortDescription' in visible:
        visible['shortDescription'] = description


def image_urls(visible):
    secondary = visible.get('productSecondaryImageURL', [])
    return list(dict.fromkeys(u.strip() for u in [visible.get('mainImageUrl', ''), *(secondary if isinstance(secondary, list) else [])]
                              if isinstance(u, str) and u.strip()))


def image_policy(visible, *, default_selection=False):
    urls = image_urls(visible)
    if default_selection:
        urls = urls[:DEFAULT_IMAGE_COUNT]
    if urls:
        visible['mainImageUrl'] = urls[0]
    if 'productSecondaryImageURL' in visible or len(urls) > 1:
        visible['productSecondaryImageURL'] = urls[1:]


def normalize(visible):
    copy_policy(visible)
    image_policy(visible)
