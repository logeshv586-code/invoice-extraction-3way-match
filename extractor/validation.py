from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from difflib import SequenceMatcher

from .config import Settings
from .db import ERPRepository
from .gst import indian_financial_year, normalize_invoice_number, valid_gst_rate, valid_gstin
from .models import InvoiceExtraction, Reason

REQUIRED_CONFIDENCE_FIELDS = (
    "vendor_gstin",
    "buyer_gstin",
    "invoice_number",
    "invoice_date",
    "grand_total",
    "line_items",
)


def _reason(code: str, message: str) -> Reason:
    return Reason(code=code, message=message)


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _line_amount(qty: float, rate_paise: int) -> int:
    value = Decimal(str(qty)) * Decimal(rate_paise)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _best_po_line(invoice_line, po_lines, used: set[int]):
    best = None
    best_score = -1.0
    for row in po_lines:
        line_no = int(row["line_no"])
        if line_no in used:
            continue
        score = 0.0
        if str(row["hsn"]).strip() == invoice_line.hsn.strip():
            score += 2.0
        desc_score = SequenceMatcher(
            None,
            str(row["description"]).lower().strip(),
            invoice_line.description.lower().strip(),
        ).ratio()
        score += desc_score
        if score > best_score:
            best, best_score = row, score
    return best


def validate_invoice(extraction: InvoiceExtraction, db: ERPRepository, settings: Settings) -> list[Reason]:
    reasons: list[Reason] = []

    if not extraction.is_invoice:
        return [_reason("NOT_AN_INVOICE", "The document was classified as not being an invoice.")]

    required_values = {
        "vendor_gstin": extraction.vendor_gstin,
        "buyer_gstin": extraction.buyer_gstin,
        "invoice_number": extraction.invoice_number,
        "invoice_date": extraction.invoice_date,
        "grand_total": extraction.grand_total,
        "line_items": extraction.line_items,
    }
    for name, value in required_values.items():
        if value is None or value == "" or (name == "line_items" and not value):
            reasons.append(_reason("MISSING_REQUIRED_FIELD", f"Required field `{name}` is missing."))
        confidence = extraction.confidence.get(name, 0.0)
        if confidence < settings.confidence_threshold:
            reasons.append(
                _reason(
                    "LOW_CONFIDENCE",
                    f"`{name}` confidence {confidence:.2f} is below threshold {settings.confidence_threshold:.2f}.",
                )
            )

    if extraction.invoice_date and extraction.invoice_date > settings.today:
        reasons.append(_reason("FUTURE_INVOICE_DATE", "Invoice date is later than configured TODAY."))

    if extraction.vendor_gstin and not valid_gstin(extraction.vendor_gstin):
        reasons.append(_reason("INVALID_VENDOR_GSTIN", "Vendor GSTIN fails format or checksum validation."))
    if extraction.buyer_gstin:
        buyer_checksum_ok = valid_gstin(extraction.buyer_gstin)
        exception = (
            settings.allow_configured_buyer_checksum_exception
            and extraction.buyer_gstin == settings.buyer_gstin
        )
        if not buyer_checksum_ok and not exception:
            reasons.append(_reason("INVALID_BUYER_GSTIN", "Buyer GSTIN fails format or checksum validation."))
        if extraction.buyer_gstin != settings.buyer_gstin:
            reasons.append(_reason("WRONG_BUYER_GSTIN", "Buyer GSTIN does not match the configured company GSTIN."))

    if extraction.invoice_date:
        for idx, line in enumerate(extraction.line_items, start=1):
            if not valid_gst_rate(float(line.gst_rate), extraction.invoice_date):
                reasons.append(
                    _reason("INVALID_GST_RATE", f"Line {idx} GST rate {line.gst_rate}% is invalid for the invoice date.")
                )
            expected = _line_amount(line.qty, line.rate)
            if abs(expected - line.taxable_value) > 100:
                reasons.append(
                    _reason(
                        "LINE_ARITHMETIC_MISMATCH",
                        f"Line {idx}: qty × rate differs from taxable value by more than ₹1.00.",
                    )
                )

    if extraction.vendor_gstin and extraction.place_of_supply:
        intra_state = extraction.vendor_gstin[:2] == extraction.place_of_supply
        if intra_state:
            if extraction.igst != 0:
                reasons.append(_reason("INVALID_TAX_TYPE", "Intra-state supply must not contain IGST."))
            if abs(extraction.cgst - extraction.sgst) > 100:
                reasons.append(_reason("INVALID_TAX_SPLIT", "CGST and SGST must be approximately equal for intra-state supply."))
        else:
            if extraction.cgst != 0 or extraction.sgst != 0:
                reasons.append(_reason("INVALID_TAX_TYPE", "Inter-state supply must use IGST, not CGST/SGST."))

    if abs(extraction.round_off) > 100:
        reasons.append(_reason("ROUND_OFF_OUT_OF_RANGE", "Absolute round-off exceeds ₹1.00."))
    expected_total = (
        sum(line.taxable_value for line in extraction.line_items)
        + extraction.cgst
        + extraction.sgst
        + extraction.igst
        + extraction.round_off
    )
    if extraction.line_items and abs(expected_total - extraction.grand_total) > 100:
        reasons.append(_reason("TOTAL_MISMATCH", "Line totals + taxes + round-off do not match grand total within ₹1.00."))

    if extraction.vendor_gstin and extraction.invoice_number and extraction.invoice_date:
        target_fy = indian_financial_year(extraction.invoice_date)
        normalized = normalize_invoice_number(extraction.invoice_number)
        for row in db.booked_invoices_for_vendor(extraction.vendor_gstin):
            booked_date = _date(row["invoice_date"])
            if (
                booked_date
                and indian_financial_year(booked_date) == target_fy
                and normalize_invoice_number(row["invoice_number"]) == normalized
            ):
                reasons.append(_reason("DUPLICATE_INVOICE", "Same vendor and normalized invoice number already exists in this financial year."))
                break

    if not extraction.po_number:
        reasons.append(_reason("PO_MISSING", "No purchase order number was extracted, so 3-way matching cannot complete."))
        return reasons

    po = db.purchase_order(extraction.po_number)
    if not po:
        reasons.append(_reason("PO_NOT_FOUND", f"Purchase order {extraction.po_number} was not found in ERP data."))
        return reasons
    if po["status"] != "OPEN":
        reasons.append(_reason("PO_NOT_OPEN", f"Purchase order status is {po['status']}, not OPEN."))

    vendor = db.vendor_by_gstin(extraction.vendor_gstin) if extraction.vendor_gstin else None
    if not vendor:
        reasons.append(_reason("VENDOR_NOT_FOUND", "Vendor GSTIN was not found in ERP vendor master."))
    elif vendor["vendor_id"] != po["vendor_id"]:
        reasons.append(_reason("PO_VENDOR_MISMATCH", "Invoice vendor does not match the purchase order vendor."))

    po_lines = db.po_lines(extraction.po_number)
    used: set[int] = set()
    for idx, line in enumerate(extraction.line_items, start=1):
        matched = _best_po_line(line, po_lines, used)
        if not matched:
            reasons.append(_reason("PO_LINE_NOT_FOUND", f"Invoice line {idx} could not be matched to a PO line."))
            continue
        line_no = int(matched["line_no"])
        used.add(line_no)

        if line.hsn.strip() != str(matched["hsn"]).strip():
            reasons.append(_reason("HSN_MISMATCH", f"Invoice line {idx} HSN does not match PO line {line_no}."))
        if abs(float(line.gst_rate) - float(matched["gst_rate"])) > 1e-9:
            reasons.append(_reason("GST_RATE_MISMATCH", f"Invoice line {idx} GST rate does not match PO line {line_no}."))

        po_rate = int(matched["rate_paise"])
        if po_rate <= 0 or abs(line.rate - po_rate) / po_rate > 0.02:
            reasons.append(_reason("RATE_OUTSIDE_TOLERANCE", f"Invoice line {idx} rate differs from PO rate by more than 2%."))

        received = db.received_qty(extraction.po_number, line_no)
        already_billed = db.already_billed_qty(extraction.po_number, line_no)
        if already_billed + float(line.qty) > received + 1e-9:
            reasons.append(
                _reason(
                    "QTY_EXCEEDS_RECEIPT",
                    f"Invoice line {idx} would bill {already_billed + float(line.qty):g} units against {received:g} received.",
                )
            )

    return reasons
