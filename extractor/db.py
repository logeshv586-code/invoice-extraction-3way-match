from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .config import Settings


class ERPRepositoryProtocol(Protocol):
    def vendor_by_gstin(self, gstin: str): ...
    def purchase_order(self, po_number: str): ...
    def po_lines(self, po_number: str): ...
    def received_qty(self, po_number: str, line_no: int) -> float: ...
    def already_billed_qty(self, po_number: str, line_no: int) -> float: ...
    def booked_invoices_for_vendor(self, vendor_gstin: str): ...


class SQLiteERPRepository:
    """Assessment/default ERP adapter backed by the supplied samples/erp.db."""

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


class PostgresERPRepository:
    """Optional production-like adapter. Schema is expected to match the assessment tables."""

    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("POSTGRES_DSN is required when DB_BACKEND=postgres")
        self.dsn = dsn

    def connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL support is optional. Install: pip install -r requirements-postgres.txt"
            ) from exc
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def vendor_by_gstin(self, gstin: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT vendor_id, name, gstin FROM vendors WHERE UPPER(gstin)=UPPER(%s)", (gstin,)
            ).fetchone()

    def purchase_order(self, po_number: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT po_number, vendor_id, po_date, status FROM purchase_orders WHERE po_number=%s",
                (po_number,),
            ).fetchone()

    def po_lines(self, po_number: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM po_lines WHERE po_number=%s ORDER BY line_no", (po_number,)
            ).fetchall()

    def received_qty(self, po_number: str, line_no: int) -> float:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(received_qty),0) AS qty FROM grn_lines WHERE po_number=%s AND po_line_no=%s",
                (po_number, line_no),
            ).fetchone()
            return float(row["qty"])

    def already_billed_qty(self, po_number: str, line_no: int) -> float:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(billed_qty),0) AS qty FROM booked_invoices WHERE po_number=%s AND po_line_no=%s",
                (po_number, line_no),
            ).fetchone()
            return float(row["qty"])

    def booked_invoices_for_vendor(self, vendor_gstin: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT vendor_gstin, invoice_number, invoice_date, po_number, po_line_no, billed_qty, total_paise "
                "FROM booked_invoices WHERE UPPER(vendor_gstin)=UPPER(%s)",
                (vendor_gstin,),
            ).fetchall()


def build_erp_repository(settings: "Settings") -> ERPRepositoryProtocol:
    backend = settings.db_backend.lower()
    if backend == "sqlite":
        return SQLiteERPRepository(settings.erp_db)
    if backend in {"postgres", "postgresql"}:
        return PostgresERPRepository(settings.postgres_dsn)
    raise ValueError(f"Unsupported DB_BACKEND={settings.db_backend!r}; use sqlite or postgres")


# Backward-compatible name used by existing unit tests and imports.
ERPRepository = SQLiteERPRepository
