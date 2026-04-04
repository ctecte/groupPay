"""Generate valid PayNow QR codes using EMVCo TLV format."""


def _tlv(tag: str, value: str) -> str:
    """Build a TLV field: 2-char tag + 2-char length + value."""
    return f"{tag}{len(value):02d}{value}"


def _crc16(data: str) -> str:
    """CRC-16/CCITT-FALSE used by EMVCo QR codes."""
    crc = 0xFFFF
    for ch in data:
        crc ^= ord(ch) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc &= 0xFFFF
    return crc.to_bytes(2, "big").hex().upper()


def generate_paynow_qr_data(
    phone: str,
    amount: str,
    payee_name: str = "GROUPPAY",
    editable: bool = False,
    one_time: bool = True,
    reference: str = "",
) -> str:
    """
    Generate EMVCo-compliant PayNow QR code data string.

    Args:
        phone: SG phone number (8 digits, no country code prefix)
        amount: Payment amount as string e.g. "12.50"
        payee_name: Display name for the payee
        editable: Whether amount is editable by payer
        one_time: True for single-use QR, False for reusable
    """
    # Ensure phone has +65 prefix
    phone_clean = phone.replace("+", "").replace(" ", "").replace("-", "")
    if phone_clean.startswith("65") and len(phone_clean) == 10:
        phone_full = f"+{phone_clean}"
    elif len(phone_clean) == 8:
        phone_full = f"+65{phone_clean}"
    else:
        phone_full = f"+65{phone_clean}"

    # Tag 26: Merchant Account Information (PayNow)
    tag26_inner = (
        _tlv("00", "SG.PAYNOW")
        + _tlv("01", "0")  # 0 = mobile number
        + _tlv("02", phone_full)
        + _tlv("03", "1" if editable else "0")
    )

    # Truncate payee name to 25 chars (EMVCo limit)
    name = payee_name[:25].upper()

    payload = (
        _tlv("00", "01")  # Payload Format Indicator
        + _tlv("01", "12" if one_time else "11")  # Point of Initiation
        + _tlv("26", tag26_inner)  # Merchant Account Info
        + _tlv("52", "0000")  # MCC (not applicable)
        + _tlv("53", "702")  # Currency: SGD
        + _tlv("54", amount)  # Transaction Amount
        + _tlv("58", "SG")  # Country
        + _tlv("59", name)  # Merchant Name
        + _tlv("60", "Singapore")  # City
    )

    # Tag 62: Additional Data — bill/reference number for traceability
    if reference:
        payload += _tlv("62", _tlv("01", reference[:25]))

    # CRC placeholder — tag 63, length 04, then compute
    payload += "6304"
    crc = _crc16(payload)
    return payload + crc
