"""
Receipt OCR module using PaddleOCR.
Extracts line items (name, price, qty) from receipt images.
"""

import io
import re
import tempfile
import threading

from PIL import Image, ImageOps


_lock = threading.Lock()


def _create_ocr():
    """Create a fresh PaddleOCR instance."""
    from paddleocr import PaddleOCR
    return PaddleOCR(use_angle_cls=True, lang='en', show_log=False)


# Lines to skip — headers, footers, payment methods, etc.
_SKIP_PATTERNS = re.compile(
    r'^\s*$|'
    r'^(receipt|invoice|tax\s*invoice|bill|order|check|ticket)\s*$|'
    r'(thank\s*you|welcome|please\s*come|visit\s*again)|'
    r'^(date|time|cashier|server|table|terminal|reg|ref|trn|auth|order\s*#?|receipt\s*#?|bill\s*#?)\s*[:.]|'
    r'^(cash|visa|master|amex|nets|credit|debit|change|tendered|rounding|round)\b|'
    r'(www\.|\.com|\.sg|tel:|phone:|fax:|address:)|'
    r'^\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}\s*$|'  # date-only lines
    r'^\d{1,2}:\d{2}\s*(am|pm)?\s*$|'  # time-only lines
    r'^#{4,}|^-{4,}|^={4,}|^\*{4,}|'  # separator lines
    r'pax\s*\d|'  # PAX count
    r'^\d+[/\-]\d+[/\-]\d+\s+\d+:\d+',  # date+time lines
    re.IGNORECASE
)

# Total/subtotal lines — skip these (frontend computes its own total)
# Use character class [l1i] to handle OCR confusion between l/1/i
_TOTAL_PATTERNS = re.compile(
    r'(sub[.\-\s]*tota[l1i]?|tota[l1i]?\b|grand[.\-\s]*tota[l1i]?|nett|net[.\-\s]*tota[l1i]?|amount[.\-\s]*due|ba[l1i]ance[.\-\s]*due|tota[l1i]?\s*qty)',
    re.IGNORECASE
)

# Tax/service charge lines — keep these
_TAX_SERVICE_PATTERNS = re.compile(
    r'\b(gst|tax|svc|service\s*charge|svc\s*chg|s\.c\.|sc\b)',
    re.IGNORECASE
)

# Singapore dollar price: S$9.90, $9.90, or just 9.90
_SG_PRICE_RE = re.compile(r'S?\$?\s*(\d+\.\d{2})\b')

# Qty prefix at start of line: "1x", "1 x", "2x", "2 x"
_QTY_PREFIX_RE = re.compile(r'^(\d+)\s*[xX×]\s+')

# Qty x price pattern anywhere: "2 x $9.90"
_QTY_PRICE_RE = re.compile(r'(\d+)\s*[xX×]\s*S?\$?\s*(\d+\.\d{2})')


def _group_by_y(results):
    """Group OCR text boxes into rows by Y-coordinate proximity.
    Uses adaptive threshold based on text box height."""
    if not results or not results[0]:
        return []

    boxes = []
    for item in results[0]:
        box_coords, (text, confidence) = item
        avg_y = (box_coords[0][1] + box_coords[1][1]) / 2
        avg_x = (box_coords[0][0] + box_coords[1][0]) / 2
        box_h = abs(box_coords[3][1] - box_coords[0][1])
        boxes.append({'text': text.strip(), 'x': avg_x, 'y': avg_y, 'h': box_h, 'conf': confidence})

    # Sort by Y then X
    boxes.sort(key=lambda b: (b['y'], b['x']))

    # Threshold = half the median box height (conservative — same-line boxes only)
    heights = sorted(b['h'] for b in boxes)
    median_h = heights[len(heights) // 2] if heights else 20
    threshold = max(15, median_h * 0.6)

    rows = []
    current_row = [boxes[0]]
    for box in boxes[1:]:
        if abs(box['y'] - current_row[0]['y']) <= threshold:
            current_row.append(box)
        else:
            rows.append(current_row)
            current_row = [box]
    rows.append(current_row)

    # Merge each row into a single text line (sorted left-to-right)
    lines = []
    for row in rows:
        row.sort(key=lambda b: b['x'])
        line = '  '.join(b['text'] for b in row)
        lines.append(line)

    return lines


# Price-only line pattern: just S$xx.xx or $xx.xx
_PRICE_ONLY_RE = re.compile(r'^S?\$?\s*\d+\.\d{2}\s*$')


def _merge_split_lines(lines):
    """Merge adjacent lines where name and price got split into separate OCR rows.
    E.g. '1 x STu Karubi Set' followed by 'S$9.90' -> '1 x STu Karubi Set  S$9.90'
    """
    merged = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # If next line is a price-only line and current line has no price, merge them
        if (i + 1 < len(lines)
                and not _SG_PRICE_RE.search(line)
                and _PRICE_ONLY_RE.match(lines[i + 1])):
            merged.append(line + '  ' + lines[i + 1])
            i += 2
        else:
            merged.append(line)
            i += 1
    return merged


def _clean_name(name):
    """Clean up an extracted item name."""
    # Remove leading qty like "1x ", "2 x "
    name = re.sub(r'^\d+\s*[xX×]\s+', '', name)
    # Remove leading/trailing S$ artifacts
    name = re.sub(r'\s*S?\$?\s*$', '', name)
    name = re.sub(r'^S?\$\s*', '', name)
    # Remove leading digits that are sub-item counts (e.g. "1:", "1 ", "1")
    name = re.sub(r'^\d+\s*:\s*', '', name)
    name = re.sub(r'^(\d+)(?=[A-Z])', '', name)  # "1Bonito" -> "Bonito"
    # Trim punctuation and whitespace
    name = re.sub(r'^[\s\-\.\*#:]+|[\s\-\.\*#:]+$', '', name)
    name = re.sub(r'\s{2,}', ' ', name)
    return name.strip()


def parse_receipt_lines(lines):
    """Parse OCR text lines into structured receipt items."""
    items = []

    for line in lines:
        print(f"[OCR LINE] {line!r}")

        # Skip obvious non-item lines
        if _SKIP_PATTERNS.search(line):
            print(f"  -> SKIP (header/footer)")
            continue

        # Skip total/subtotal lines
        if _TOTAL_PATTERNS.search(line):
            print(f"  -> SKIP (total)")
            continue

        # Find all S$/$ prices in the line
        prices = _SG_PRICE_RE.findall(line)
        if not prices:
            print(f"  -> SKIP (no price found)")
            continue

        # Use the rightmost (last) price as the item price
        price = float(prices[-1])
        if price <= 0:
            continue

        qty = 1
        name = line

        # Try qty prefix at start: "1x STU Karubi Set S$9.90"
        qty_prefix = _QTY_PREFIX_RE.match(line)
        if qty_prefix:
            qty = int(qty_prefix.group(1))
            # Strip the "1x " prefix and the price from the name
            name = line[qty_prefix.end():]
            name = _SG_PRICE_RE.sub('', name)
        else:
            # Try qty x price pattern: "2 x $9.90"
            qty_match = _QTY_PRICE_RE.search(line)
            if qty_match:
                qty = int(qty_match.group(1))
                unit_price = float(qty_match.group(2))
                if abs(qty * unit_price - price) < 0.02 and qty > 1:
                    price = unit_price
                name = line[:qty_match.start()]
                if not name.strip():
                    name = _QTY_PRICE_RE.sub('', line)
            else:
                # Just strip the price from the line
                name = _SG_PRICE_RE.sub('', line)

        name = _clean_name(name)

        if not name or len(name) < 2:
            print(f"  -> SKIP (name too short after cleanup)")
            continue

        # For tax/service charge lines, always qty=1
        if _TAX_SERVICE_PATTERNS.search(name):
            qty = 1

        print(f"  -> KEEP: name={name!r}, price={price}, qty={qty}")
        items.append({'name': name, 'price': price, 'qty': qty})

    print(f"[OCR] Final items: {items}")
    return items


def run_ocr(image_bytes):
    """
    Run OCR on receipt image bytes.
    Returns list of {"name": str, "price": float, "qty": int}.
    """
    # Fix EXIF orientation
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)
    img = img.convert('RGB')

    # Save to temp file for PaddleOCR
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
        img.save(tmp, format='JPEG', quality=95)
        tmp_path = tmp.name

    with _lock:
        ocr = _create_ocr()
        results = ocr.ocr(tmp_path)

    import os
    os.unlink(tmp_path)

    if not results or not results[0]:
        return []

    lines = _group_by_y(results)
    lines = _merge_split_lines(lines)
    items = parse_receipt_lines(lines)

    return items
