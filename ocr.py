"""
Receipt OCR module using PaddleOCR.
Extracts line items (name, price, qty) from receipt images.
"""

import io
import os
import re
import tempfile
from concurrent.futures import ProcessPoolExecutor

from PIL import Image, ImageOps


# Single-worker process pool — model loads once in the subprocess and stays warm
_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=1)
    return _pool


def _ocr_in_subprocess(tmp_path):
    """Run PaddleOCR in an isolated subprocess. Model stays loaded between calls."""
    global _subprocess_ocr
    try:
        _subprocess_ocr
    except NameError:
        from paddleocr import PaddleOCR
        _subprocess_ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)
    return _subprocess_ocr.ocr(tmp_path)


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
    r'^\d+[/\-]\d+[/\-]\d+\s+\d+:\d+|'  # date+time lines
    # Payment terminal / card slip lines
    r'\b(MID|TID|BATCH|TRACE|INV|ECR|APPR|S/W|CONTACTLESS|PAYPASS|PAYWAVE|CHIP|PIN|OFFUS)\b|'
    r'\b(MASTER\s*CARD|VISA\s*CARD|CREDIT\s*CARD|DEBIT\s*CARD)\b|'
    r'\b(BASE|APPROVED|DECLINED|SALE|REFUND)\b|'
    r'\*{4,}\s*\d{4}|'  # masked card number ****1234
    r'^(slip|staff|barcode|description|amount|qty|pos)\s*[:.]?$',
    re.IGNORECASE
)

# Stop parsing after these lines — everything below is payment slip / footer
_STOP_PATTERNS = re.compile(
    r'\b(credit\s*card|debit\s*card|master\s*card|visa|nets|cash\s*tendered|amount\s*tendered)\b',
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


def _extract_grand_total(lines):
    """Try to find the grand total value from receipt lines."""
    for line in lines:
        if _TOTAL_PATTERNS.search(line) and 'qty' not in line.lower() and 'sub' not in line.lower():
            prices = _SG_PRICE_RE.findall(line)
            if prices:
                return float(prices[-1])
    return None


def parse_receipt_lines(lines):
    """Parse OCR text lines into structured receipt items."""
    items = []
    stopped = False

    for line in lines:
        print(f"[OCR LINE] {line!r}")

        if stopped:
            print(f"  -> SKIP (after stop line)")
            continue

        # Stop parsing at payment slip / card lines
        if _STOP_PATTERNS.search(line):
            print(f"  -> STOP (payment method line — rest is card slip)")
            stopped = True
            continue

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

        # Tag tax/service charge lines
        is_charge = bool(_TAX_SERVICE_PATTERNS.search(name))
        if is_charge:
            qty = 1

        print(f"  -> KEEP: name={name!r}, price={price}, qty={qty}, charge={is_charge}")
        items.append({'name': name, 'price': price, 'qty': qty, 'is_charge': is_charge})

    print(f"[OCR] Final items: {items}")
    return items


def _ocr_image(img):
    """Run OCR on a PIL Image and return (parsed_items, lines)."""
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
        img.save(tmp, format='JPEG', quality=95)
        tmp_path = tmp.name

    future = _get_pool().submit(_ocr_in_subprocess, tmp_path)
    results = future.result(timeout=60)
    os.unlink(tmp_path)

    if not results or not results[0]:
        return [], []

    lines = _group_by_y(results)
    lines = _merge_split_lines(lines)
    return parse_receipt_lines(lines), lines


def run_ocr(image_bytes):
    """
    Run OCR on receipt image bytes.
    Returns dict with items, charges, charges_included.
    Tries original orientation and 90° rotation, picks the best result.
    """
    # Fix EXIF orientation
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)
    img = img.convert('RGB')

    # Try original orientation
    all_items, all_lines = _ocr_image(img)
    food_count = sum(1 for i in all_items if not i['is_charge'])

    # If landscape image and few items found, try 90° rotations
    w, h = img.size
    if w > h and food_count < 3:
        print(f"[OCR] Landscape image with only {food_count} food items — trying rotations")
        for angle in [270, 90]:
            rotated = img.rotate(angle, expand=True)
            rotated_items, rotated_lines = _ocr_image(rotated)
            rotated_food = sum(1 for i in rotated_items if not i['is_charge'])
            print(f"[OCR] Rotation {angle}°: {rotated_food} food items")
            if rotated_food > food_count:
                all_items = rotated_items
                all_lines = rotated_lines
                food_count = rotated_food

    food_items = [{'name': i['name'], 'price': i['price'], 'qty': i['qty']}
                  for i in all_items if not i['is_charge']]
    charges = [{'name': i['name'], 'price': i['price']}
               for i in all_items if i['is_charge']]

    # Detect if charges are already included in menu prices.
    grand_total = _extract_grand_total(all_lines)
    food_subtotal = sum(i['price'] * i['qty'] for i in food_items)
    charges_total = sum(c['price'] for c in charges)

    charges_included = False
    if grand_total and charges:
        # If grand total ≈ food subtotal, charges are already in the prices
        if abs(grand_total - food_subtotal) < 0.50:
            charges_included = True
            print(f"[OCR] Charges appear INCLUDED in menu prices (grand total {grand_total} ≈ food subtotal {food_subtotal})")
        # If grand total ≈ food + charges, charges are extra
        elif abs(grand_total - (food_subtotal + charges_total)) < 0.50:
            print(f"[OCR] Charges appear EXCLUDED from menu prices (grand total {grand_total} ≈ food {food_subtotal} + charges {charges_total})")

    return {'items': food_items, 'charges': charges, 'charges_included': charges_included}
