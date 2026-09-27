from __future__ import annotations

import json
import time
from pathlib import Path

from .config import Settings, settings as default_settings
from .db import ERPRepository
from .models import InvoiceExtraction, ProcessResult, Reason, RunMetrics
from .ocr import DocumentReader
from .structuring import InvoiceStructurer
from .validation import validate_invoice


class InvoicePipeline:
    def __init__(self, settings: Settings = default_settings):
        self.settings = settings
        self.reader = DocumentReader(settings)
        self.structurer = InvoiceStructurer(settings)
        self.db = ERPRepository(settings.erp_db)

    def process(self, path: str | Path) -> ProcessResult:
        path = Path(path)
        started = time.perf_counter()
        reader_name = ""
        structuring_model = ""
        tokens_in = tokens_out = retries = 0

        try:
            ocr = self.reader.read(path)
            reader_name = ocr.provider
            structured = self.structurer.structure(ocr.text)
            extraction = structured.extraction
            structuring_model = structured.model
            tokens_in = structured.tokens_in
            tokens_out = structured.tokens_out
            retries = structured.retries
            reasons = validate_invoice(extraction, self.db, self.settings)
            if not extraction.is_invoice:
                decision = "REJECTED"
            elif reasons:
                decision = "NEEDS_REVIEW"
            else:
                decision = "AUTO_APPROVE"
        except Exception as exc:
            extraction = InvoiceExtraction(is_invoice=True, confidence={})
            reasons = [Reason(code="PIPELINE_ERROR", message=str(exc))]
            decision = "NEEDS_REVIEW"

        latency_ms = int((time.perf_counter() - started) * 1000)
        cost = (
            tokens_in * self.settings.input_cost_per_1m / 1_000_000
            + tokens_out * self.settings.output_cost_per_1m / 1_000_000
        )
        result = ProcessResult(
            document=path.name,
            extraction=extraction,
            decision=decision,
            reasons=reasons,
            metrics=RunMetrics(
                reader=reader_name,
                structuring_model=structuring_model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_estimate_usd=round(cost, 8),
                latency_ms=latency_ms,
                retries=retries,
            ),
        )
        self._log_metrics(result)
        return result

    @staticmethod
    def _log_metrics(result: ProcessResult, path: Path = Path("out/run_metrics.jsonl")) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "document": result.document,
            "decision": result.decision,
            **result.metrics.model_dump(),
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
