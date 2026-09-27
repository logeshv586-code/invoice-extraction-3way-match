import sqlite3
from datetime import date
from pathlib import Path

from extractor.config import Settings
from extractor.db import ERPRepository
from extractor.models import InvoiceExtraction, LineItem
from extractor.validation import validate_invoice

BUYER = "29AAACR5055K1Z3"
VENDOR = "27AAACR5055K1Z7"


def make_db(path: Path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE vendors (vendor_id TEXT PRIMARY KEY, name TEXT NOT NULL, gstin TEXT NOT NULL UNIQUE);
        CREATE TABLE purchase_orders (po_number TEXT PRIMARY KEY, vendor_id TEXT NOT NULL, po_date TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE po_lines (po_number TEXT NOT NULL, line_no INTEGER NOT NULL, item_code TEXT NOT NULL, description TEXT NOT NULL, hsn TEXT NOT NULL, qty REAL NOT NULL, rate_paise INTEGER NOT NULL, gst_rate REAL NOT NULL, PRIMARY KEY (po_number, line_no));
        CREATE TABLE grn_lines (grn_number TEXT NOT NULL, po_number TEXT NOT NULL, po_line_no INTEGER NOT NULL, received_qty REAL NOT NULL, received_on TEXT NOT NULL);
        CREATE TABLE booked_invoices (vendor_gstin TEXT NOT NULL, invoice_number TEXT NOT NULL, invoice_date TEXT NOT NULL, po_number TEXT, po_line_no INTEGER, billed_qty REAL, total_paise INTEGER NOT NULL);
        """
    )
    conn.execute("INSERT INTO vendors VALUES (?,?,?)", ("V1", "Demo Vendor", VENDOR))
    conn.execute("INSERT INTO purchase_orders VALUES (?,?,?,?)", ("PO-1", "V1", "2026-08-01", "OPEN"))
    conn.execute("INSERT INTO po_lines VALUES (?,?,?,?,?,?,?,?)", ("PO-1", 1, "ITEM1", "Widget", "8471", 10, 10000, 18))
    conn.execute("INSERT INTO grn_lines VALUES (?,?,?,?,?)", ("GRN-1", "PO-1", 1, 7, "2026-08-05"))
    conn.commit()
    conn.close()


def settings_for(db: Path) -> Settings:
    return Settings(today=date(2026, 10, 1), buyer_gstin=BUYER, confidence_threshold=0.85, erp_db=db)


def base_invoice(qty=5, rate=10000, inv="INV-100"):
    taxable = int(qty * rate)
    igst = int(round(taxable * 0.18))
    return InvoiceExtraction(
        is_invoice=True,
        vendor_name="Demo Vendor",
        vendor_gstin=VENDOR,
        buyer_gstin=BUYER,
        invoice_number=inv,
        invoice_date=date(2026, 9, 1),
        place_of_supply="29",
        po_number="PO-1",
        line_items=[LineItem(description="Widget", hsn="8471", qty=qty, rate=rate, taxable_value=taxable, gst_rate=18)],
        cgst=0,
        sgst=0,
        igst=igst,
        round_off=0,
        grand_total=taxable + igst,
        confidence={
            "vendor_gstin": 0.99,
            "buyer_gstin": 0.99,
            "invoice_number": 0.99,
            "invoice_date": 0.99,
            "grand_total": 0.99,
            "line_items": 0.99,
        },
    )


def codes(reasons):
    return {r.code for r in reasons}


def test_happy_path_auto_approve_candidate(tmp_path):
    db = tmp_path / "erp.db"
    make_db(db)
    reasons = validate_invoice(base_invoice(qty=5), ERPRepository(db), settings_for(db))
    assert reasons == []


def test_partial_grn_blocks_overbilling(tmp_path):
    db = tmp_path / "erp.db"
    make_db(db)
    reasons = validate_invoice(base_invoice(qty=8), ERPRepository(db), settings_for(db))
    assert "QTY_EXCEEDS_RECEIPT" in codes(reasons)


def test_already_billed_plus_current_cannot_exceed_received(tmp_path):
    db = tmp_path / "erp.db"
    make_db(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO booked_invoices VALUES (?,?,?,?,?,?,?)", (VENDOR, "OLD-1", "2026-08-10", "PO-1", 1, 4, 47200))
    conn.commit(); conn.close()
    reasons = validate_invoice(base_invoice(qty=4), ERPRepository(db), settings_for(db))
    assert "QTY_EXCEEDS_RECEIPT" in codes(reasons)


def test_duplicate_same_fy_normalized_number(tmp_path):
    db = tmp_path / "erp.db"
    make_db(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO booked_invoices VALUES (?,?,?,?,?,?,?)", (VENDOR, "inv / 100", "2026-04-15", "PO-1", 1, 1, 11800))
    conn.commit(); conn.close()
    reasons = validate_invoice(base_invoice(inv="INV/100"), ERPRepository(db), settings_for(db))
    assert "DUPLICATE_INVOICE" in codes(reasons)


def test_duplicate_previous_fy_does_not_block(tmp_path):
    db = tmp_path / "erp.db"
    make_db(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO booked_invoices VALUES (?,?,?,?,?,?,?)", (VENDOR, "INV/100", "2026-03-31", "PO-1", 1, 1, 11800))
    conn.commit(); conn.close()
    reasons = validate_invoice(base_invoice(inv="INV/100"), ERPRepository(db), settings_for(db))
    assert "DUPLICATE_INVOICE" not in codes(reasons)


def test_future_date_and_rate_tolerance(tmp_path):
    db = tmp_path / "erp.db"
    make_db(db)
    invoice = base_invoice(rate=10300)
    invoice.invoice_date = date(2026, 10, 2)
    invoice.line_items[0].taxable_value = int(invoice.line_items[0].qty * invoice.line_items[0].rate)
    invoice.igst = int(round(invoice.line_items[0].taxable_value * 0.18))
    invoice.grand_total = invoice.line_items[0].taxable_value + invoice.igst
    reasons = validate_invoice(invoice, ERPRepository(db), settings_for(db))
    assert {"FUTURE_INVOICE_DATE", "RATE_OUTSIDE_TOLERANCE"}.issubset(codes(reasons))


def test_tax_type_split(tmp_path):
    db = tmp_path / "erp.db"
    make_db(db)
    invoice = base_invoice()
    invoice.place_of_supply = "27"
    invoice.cgst = 0
    invoice.sgst = 0
    invoice.igst = 9000
    reasons = validate_invoice(invoice, ERPRepository(db), settings_for(db))
    assert "INVALID_TAX_TYPE" in codes(reasons)


def test_low_confidence_blocks_auto_approval(tmp_path):
    db = tmp_path / "erp.db"
    make_db(db)
    invoice = base_invoice()
    invoice.confidence["invoice_number"] = 0.5
    reasons = validate_invoice(invoice, ERPRepository(db), settings_for(db))
    assert "LOW_CONFIDENCE" in codes(reasons)
