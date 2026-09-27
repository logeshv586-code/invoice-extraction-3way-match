from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    today: date = date.fromisoformat(os.getenv("TODAY", "2026-10-01"))
    buyer_gstin: str = os.getenv("BUYER_GSTIN", "29AABCE1234F1Z5").strip().upper()
    confidence_threshold: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.85"))
    erp_db: Path = Path(os.getenv("ERP_DB", "samples/erp.db"))

    ocr_mode: str = os.getenv("OCR_MODE", "unlimited_ocr_http")
    unlimited_ocr_url: str = os.getenv("UNLIMITED_OCR_URL", "http://127.0.0.1:10000").rstrip("/")
    unlimited_ocr_model: str = os.getenv("UNLIMITED_OCR_MODEL", "Unlimited-OCR")
    unlimited_ocr_timeout_seconds: int = int(os.getenv("UNLIMITED_OCR_TIMEOUT_SECONDS", "1200"))

    structuring_mode: str = os.getenv("STRUCTURING_MODE", "heuristic")
    structuring_api_url: str = os.getenv("STRUCTURING_API_URL", "http://127.0.0.1:11434/v1").rstrip("/")
    structuring_api_key: str = os.getenv("STRUCTURING_API_KEY", "")
    structuring_model: str = os.getenv("STRUCTURING_MODEL", "qwen2.5:7b")
    structuring_timeout_seconds: int = int(os.getenv("STRUCTURING_TIMEOUT_SECONDS", "120"))

    input_cost_per_1m: float = float(os.getenv("INPUT_COST_PER_1M", "0"))
    output_cost_per_1m: float = float(os.getenv("OUTPUT_COST_PER_1M", "0"))
    allow_configured_buyer_checksum_exception: bool = _bool(
        "ALLOW_CONFIGURED_BUYER_CHECKSUM_EXCEPTION", False
    )


settings = Settings()
