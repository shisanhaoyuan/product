"""SKU-local listing copy/layout and image normalization, independent of ERP automation.

Description policy follows digit-emperor-listing / description_format.py, with
an explicit exception: independent Key Features have priority over description.
"""
import html
import re
import unicodedata
from functools import lru_cache
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


def split_single_feature(visible, definition):
    """Skill exception: split a sole populated feature using the actual schema.

    Keep complete facts, source order and every existing multi-entry list. Never
    duplicate, truncate or invent a point just to reach minItems.
    """
    from .walmart_listing_schema import precise_validator
    original = visible.get('keyFeatures')
    if not isinstance(original, list):
        return False
    populated = [value for value in original if isinstance(value, str) and text(value)]
    if len(populated) != 1:
        return False
    validator = precise_validator(definition)
    if validator.is_valid(populated):
        return False
    raw = text(populated[0])
    raw = re.sub(r'(?i)^\s*(?:【\s*(?:Key\s+)?Features?\s*】|'
                 r'\[\s*(?:Key\s+)?Features?\s*\]|\*\*\s*(?:Key\s+)?Features?\s*:?\*\*)\s*:?\s*', '', raw)
    # A unit can also end a sentence. Preserve the unit's dot, but expose a
    # boundary before a clearly new capitalized subject, unlike "24.5 in. wide".
    raw = re.sub(r'(\b(?:in|ft|oz|lb|lbs|cm|mm)\.)(\s+)(?=(?:The|This|These|It|Its|Each|A|An)\b)',
                 r'\1\n', raw)
    points = sentences(raw)
    if not points or len(points) > 128:
        return False
    minimum = max(1, definition.get('minItems', 1))
    maximum = min(definition.get('maxItems', 128), 128)
    item_schema = definition.get('items', {})
    if not isinstance(item_schema, dict):
        return False
    item_validator = precise_validator(item_schema)

    def partition(atoms):
        @lru_cache(None)
        def pack(start, remaining):
            if remaining == 0:
                return () if start == len(atoms) else None
            for end in range(start + 1, len(atoms) - remaining + 2):
                value = ' '.join(atoms[start:end])
                if not item_validator.is_valid(value):
                    continue
                tail = pack(end, remaining - 1)
                if tail is not None:
                    return (value, *tail)
            return None
        for count in range(minimum, min(maximum, len(atoms)) + 1):
            result = pack(0, count)
            if result is not None and validator.is_valid(list(result)):
                return list(result)
        return None

    result = partition(points)
    if result is None:
        # Only split semicolons / parallel clauses that can stand independently;
        # a list of colors, materials, dimensions or noun fragments stays intact.
        expanded = []
        for point in points:
            clauses = re.split(r';\s*|,\s*(?=and\s+(?:it|the|this|these|each)\b)', point)
            independent = lambda clause: bool(re.search(r'\b\w+\b\s*:\s*\S', clause) or (
                len(re.findall(r'[A-Za-z]+', clause)) >= 3 and re.search(
                    r'\b(?:is|are|has|have|includes?|provides?|offers?|supports?|features?|fits?|keeps?|allows?|comes?|holds?)\b', clause, re.I)))
            expanded.extend(clauses if len(clauses) > 1 and all(independent(c) for c in clauses) else [point])
        if len(expanded) <= 128:
            result = partition(expanded)
    if result is None or result == original:
        return False
    visible['keyFeatures'] = result
    return True


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
    source_description = next((v for v in (source_copy.get('description'), facts.get('detail.description'))
                               if text(v).strip()), None)
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
