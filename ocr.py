"""
Receipt OCR module.
Primary: Gemini Flash vision API for accurate receipt extraction.
Fallback: PaddleOCR + regex parsing when Gemini is unavailable.
"""

import base64
import io
import json
import os
import re
import tempfile
from concurrent.futures import ProcessPoolExecutor

import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')


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
# Handle OCR confusion: o↔a↔0, l↔1↔i (e.g. "Tatal", "Tota1", "T0tal")
_TOTAL_PATTERNS = re.compile(
    r'(sub[.\-\s]*t[oa0]t[a1i][l1ia]?|t[oa0]t[a1i][l1ia]?\b|grand[.\-\s]*t[oa0]t[a1i][l1ia]?|nett|net[.\-\s]*t[oa0]t[a1i][l1ia]?|amount[.\-\s]*due|ba[l1i]ance[.\-\s]*due|t[oa0]t[a1i][l1ia]?\s*qty)',
    re.IGNORECASE
)

# Tax/service charge lines — keep these
_TAX_SERVICE_PATTERNS = re.compile(
    r'\b(gst|tax|svc|service\s*charge|svc\s*chg|s\.c\.|sc\b)',
    re.IGNORECASE
)

# Singapore dollar price: S$9.90, $9.90, or just 9.90
_SG_PRICE_RE = re.compile(r'S?\$?\s*(\d+\.\d{2})\b')

# Qty prefix at start of line: "1x ", "1 x ", "2x ", "1xName" (no space)
_QTY_PREFIX_RE = re.compile(r'^(\d+)\s*[xX×]\s+')
# No-space variant: "1xTeriyaki" — digit(s), x, then uppercase letter
_QTY_NOSPACE_RE = re.compile(r'^(\d+)[xX×]([A-Z])')
# Bare "X " prefix — OCR splits "1x" across lines leaving just "X  ..."
_BARE_X_RE = re.compile(r'^[xX×]\s{2,}')

# Qty x price pattern anywhere: "2 x $9.90"
_QTY_PRICE_RE = re.compile(r'(\d+)\s*[xX×]\s*S?\$?\s*(\d+\.\d{2})')


def _to_money(value):
    """Best-effort conversion of model output into a 2dp float."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    if isinstance(value, str):
        match = _SG_PRICE_RE.search(value.replace(",", ""))
        if match:
            return round(float(match.group(1)), 2)
        try:
            return round(float(value.strip()), 2)
        except ValueError:
            return None
    return None


def _normalize_model_items(items):
    normalized = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        price = _to_money(item.get("price"))
        qty = item.get("qty", 1)
        try:
            qty = max(1, int(qty))
        except (TypeError, ValueError):
            qty = 1
        if not name or price is None:
            continue
        normalized.append({"name": name, "price": price, "qty": qty})
    return normalized


def _normalize_model_charges(charges):
    normalized = []
    for charge in charges or []:
        if not isinstance(charge, dict):
            continue
        name = str(charge.get("name", "")).strip()
        price = _to_money(charge.get("price"))
        if not name or price is None:
            continue
        normalized.append({"name": name, "price": price})
    return normalized


def _normalize_gemini_result(result):
    """Accept the new schema and map older responses into it."""
    items = _normalize_model_items(result.get("items", []))
    add_on_charges = _normalize_model_charges(
        result.get("add_on_charges", result.get("charges", []))
    )
    informational_charges = _normalize_model_charges(result.get("informational_charges", []))
    charges_included = bool(result.get("charges_included", False))

    # Backward compatibility for the previous schema: a single `charges` list plus boolean.
    if not result.get("add_on_charges") and not result.get("informational_charges") and result.get("charges"):
        if charges_included:
            informational_charges = _normalize_model_charges(result.get("charges", []))
            add_on_charges = []
        else:
            add_on_charges = _normalize_model_charges(result.get("charges", []))
            informational_charges = []

    receipt_subtotal = _to_money(result.get("receipt_subtotal"))
    receipt_grand_total = _to_money(result.get("receipt_grand_total"))

    return {
        "items": items,
        "add_on_charges": add_on_charges,
        "informational_charges": informational_charges,
        "charges": add_on_charges + informational_charges,
        "charges_included": bool(informational_charges) and not bool(add_on_charges),
        "receipt_subtotal": receipt_subtotal,
        "receipt_grand_total": receipt_grand_total,
    }


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

        # Detect qty prefix: "1x ...", "1xName...", or bare "X ..."
        detected_qty = 1
        line_after_qty = line
        qty_m = _QTY_PREFIX_RE.match(line)
        nospace_m = _QTY_NOSPACE_RE.match(line) if not qty_m else None
        bare_m = _BARE_X_RE.match(line) if not qty_m and not nospace_m else None

        if qty_m:
            detected_qty = int(qty_m.group(1))
            line_after_qty = line[qty_m.end():]
        elif nospace_m:
            detected_qty = int(nospace_m.group(1))
            # Keep the uppercase letter that was glued to "x"
            line_after_qty = nospace_m.group(2) + line[nospace_m.end():]
        elif bare_m:
            detected_qty = 1
            line_after_qty = line[bare_m.end():]

        has_qty = bool(qty_m or nospace_m or bare_m)

        # Find all S$/$ prices in the line
        prices = _SG_PRICE_RE.findall(line)

        if not prices:
            if has_qty:
                # Qty prefix but no valid price — likely a garbled price.
                # Keep with price=0 so user can edit. (May also catch sub-items
                # on set-meal receipts, but those are easier to delete than
                # re-add a missing item.)
                print(f"  -> KEEP with price=0 (qty prefix but no price)")
                name = line_after_qty
                # Strip trailing garbled price/OCR noise from name.
                words = name.split()
                while words:
                    w = words[-1]
                    alpha = sum(1 for c in w if c.isalpha())
                    if alpha < len(w) * 0.5 or len(w) <= 2:
                        words.pop()
                    else:
                        break
                name = ' '.join(words)
                name = _clean_name(name)
                if name and len(name) >= 2:
                    items.append({'name': name, 'price': 0, 'qty': detected_qty, 'is_charge': False})
                continue
            print(f"  -> SKIP (no price found)")
            continue

        # Use the rightmost (last) price as the item price
        price = float(prices[-1])
        if price <= 0:
            continue

        qty = detected_qty
        name = line

        if has_qty:
            # Strip the qty prefix; take text before the first price match
            # (text after the price is usually background noise from OCR)
            name = line_after_qty
            price_match = _SG_PRICE_RE.search(name)
            if price_match:
                name = name[:price_match.start()]
            else:
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
            if price > 0 and has_qty:
                # Price detected but name got lost (OCR split) — keep as unnamed
                print(f"  -> KEEP: unnamed item, price={price}, qty={qty}")
                items.append({'name': '(unnamed item)', 'price': price, 'qty': qty, 'is_charge': False})
            else:
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


def _gemini_ocr(image_bytes):
    """Extract receipt items using Gemini Flash vision API."""
    if not GEMINI_API_KEY:
        return None

    b64 = base64.b64encode(image_bytes).decode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
                {"text": (
                    "Extract the bill structure from this receipt image. "
                    "Return ONLY valid JSON with this exact structure, no markdown:\n"
                    '{"items": [{"name": "Item Name", "price": 9.90, "qty": 1}], '
                    '"add_on_charges": [{"name": "Service Charge 10%", "price": 1.50}], '
                    '"informational_charges": [{"name": "GST 9% (included)", "price": 0.99}], '
                    '"receipt_subtotal": 18.00, '
                    '"receipt_grand_total": 19.50}\n\n'
                    "Rules:\n"
                    "- items: individual food/drink items with name, unit price, quantity\n"
                    "- add_on_charges: GST/service charge lines that are actually added on top of menu prices and contribute to the amount payable\n"
                    "- informational_charges: tax/service lines shown only for breakdown/info and already included in the payable amount; common clues are 'included', parentheses, or grand total not increasing by that amount\n"
                    "- receipt_subtotal: copy the subtotal before add-on charges if it is explicitly visible; otherwise null\n"
                    "- receipt_grand_total: copy the final amount payable exactly as shown on the receipt; do NOT invent or recompute it; if not visible, return null\n"
                    "- Never use informational_charges to build a new total\n"
                    "- If a receipt shows both informational GST and add-on service charge, keep them in separate arrays\n"
                    "- Skip sub-items that are part of a set meal (e.g. rice, salad that come with a set)\n"
                    "- Skip headers, footers, payment method lines\n"
                    "- Use the unit price, not the line total for multi-qty items"
                )}
            ]
        }],
        "generationConfig": {"temperature": 0}
    }

    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 429:
            print("[GEMINI] Rate limited, falling back to PaddleOCR")
            return None
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        # Strip markdown code fences if present
        text = re.sub(r'^```json\s*', '', text.strip())
        text = re.sub(r'\s*```$', '', text.strip())
        result = _normalize_gemini_result(json.loads(text))
        if not result["items"]:
            print("[GEMINI] No items returned, falling back to PaddleOCR")
            return None
        print(
            f"[GEMINI] Extracted {len(result['items'])} items, "
            f"{len(result['add_on_charges'])} add-on charges, "
            f"{len(result['informational_charges'])} informational charges, "
            f"grand_total={result['receipt_grand_total']}"
        )
        return result
    except Exception as e:
        print(f"[GEMINI] Failed: {e}, falling back to PaddleOCR")
        return None


def run_ocr(image_bytes):
    """
    Run OCR on receipt image bytes.
    Returns dict with items, charges, charges_included.
    Primary: Gemini Flash. Fallback: PaddleOCR with rotation/preprocessing.
    """
    # Try Gemini first
    gemini_result = _gemini_ocr(image_bytes)
    if gemini_result:
        return gemini_result
    # Fix EXIF orientation
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)
    img = img.convert('RGB')

    # Preprocess: improve contrast and sharpen for better OCR accuracy
    img = ImageEnhance.Contrast(img).enhance(1.5)
    img = img.filter(ImageFilter.SHARPEN)

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

    return {
        'items': food_items,
        'add_on_charges': [] if charges_included else charges,
        'informational_charges': charges if charges_included else [],
        'charges': charges,
        'charges_included': charges_included,
        'receipt_subtotal': round(food_subtotal, 2),
        'receipt_grand_total': round(grand_total, 2) if grand_total is not None else None,
    }
