from __future__ import annotations

import re
from datetime import date

GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_check_digit(first14: str) -> str:
    """Return the standard Luhn Mod-36 GSTIN check digit."""
    if len(first14) != 14 or any(ch not in CHARSET for ch in first14):
        raise ValueError("GSTIN checksum input must contain 14 base-36 characters")
    total = 0
    for index, character in enumerate(first14):
        product = CHARSET.index(character) * (1 if index % 2 == 0 else 2)
        total += product // 36 + product % 36
    return CHARSET[(36 - total % 36) % 36]


def valid_gstin(gstin: str | None) -> bool:
    if not gstin:
        return False
    value = gstin.replace(" ", "").upper()
    return bool(GSTIN_PATTERN.fullmatch(value)) and gstin_check_digit(value[:14]) == value[14]


def valid_gst_rate(rate: float, invoice_date: date) -> bool:
    common = {0.0, 0.25, 3.0, 5.0}
    if invoice_date < date(2025, 9, 22):
        return rate in common | {12.0, 18.0, 28.0}
    return rate in common | {18.0, 40.0}


def indian_financial_year(value: date) -> tuple[int, int]:
    start = value.year if value.month >= 4 else value.year - 1
    return start, start + 1


def normalize_invoice_number(value: str | None) -> str:
    return "".join((value or "").upper().split())
