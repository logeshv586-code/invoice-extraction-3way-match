from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class LineItem(BaseModel):
    description: str = ""
    hsn: str = ""
    qty: float
    rate: int = Field(description="Integer paise per unit")
    taxable_value: int = Field(description="Integer paise")
    gst_rate: float

    @field_validator("rate", "taxable_value")
    @classmethod
    def money_must_be_integer_paise(cls, value: int) -> int:
        if not isinstance(value, int):
            raise ValueError("money must be integer paise")
        return value


class InvoiceExtraction(BaseModel):
    is_invoice: bool
    vendor_name: str | None = None
    vendor_gstin: str | None = None
    buyer_gstin: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    place_of_supply: str | None = None
    po_number: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    cgst: int = 0
    sgst: int = 0
    igst: int = 0
    round_off: int = 0
    grand_total: int = 0
    confidence: dict[str, float] = Field(default_factory=dict)

    @field_validator("vendor_gstin", "buyer_gstin")
    @classmethod
    def normalize_gstin(cls, value: str | None) -> str | None:
        return value.replace(" ", "").upper() if value else value

    @field_validator("place_of_supply")
    @classmethod
    def normalize_state_code(cls, value: str | None) -> str | None:
        if value is None:
            return value
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        return digits[:2].zfill(2) if digits else None


class Reason(BaseModel):
    code: str
    message: str


class RunMetrics(BaseModel):
    reader: str = ""
    structuring_model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_estimate_usd: float = 0.0
    latency_ms: int = 0
    retries: int = 0


class ProcessResult(BaseModel):
    document: str
    extraction: InvoiceExtraction
    decision: Literal["AUTO_APPROVE", "NEEDS_REVIEW", "REJECTED"]
    reasons: list[Reason] = Field(default_factory=list)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
