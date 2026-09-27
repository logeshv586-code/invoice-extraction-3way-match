from __future__ import annotations

import sqlite3
from pathlib import Path


class ERPRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"ERP database not found: {self.path}")

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def vendor_by_gstin(self, gstin: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT vendor_id, name, gstin FROM vendors WHERE UPPER(gstin)=UPPER(?)", (gstin,)
            ).fetchone()

    def purchase_order(self, po_number: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT po_number, vendor_id, po_date, status FROM purchase_orders WHERE po_number=?",
                (po_number,),
            ).fetchone()

    def po_lines(self, po_number: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM po_lines WHERE po_number=? ORDER BY line_no", (po_number,)
            ).fetchall()

    def received_qty(self, po_number: str, line_no: int) -> float:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(received_qty),0) AS qty FROM grn_lines WHERE po_number=? AND po_line_no=?",
                (po_number, line_no),
            ).fetchone()
            return float(row["qty"])

    def already_billed_qty(self, po_number: str, line_no: int) -> float:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(billed_qty),0) AS qty FROM booked_invoices WHERE po_number=? AND po_line_no=?",
                (po_number, line_no),
            ).fetchone()
            return float(row["qty"])

    def booked_invoices_for_vendor(self, vendor_gstin: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT vendor_gstin, invoice_number, invoice_date, po_number, po_line_no, billed_qty, total_paise "
                "FROM booked_invoices WHERE UPPER(vendor_gstin)=UPPER(?)",
                (vendor_gstin,),
            ).fetchall()
