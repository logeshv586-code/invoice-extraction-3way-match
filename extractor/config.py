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

    # ERP database. SQLite is the assessment/default path. PostgreSQL is optional.
    db_backend: str = os.getenv("DB_BACKEND", "sqlite").strip().lower()
    erp_db: Path = Path(os.getenv("ERP_DB", "samples/erp.db"))
    postgres_dsn: str = os.getenv("POSTGRES_DSN", "").strip()

    # OCR primary.
    ocr_mode: str = os.getenv("OCR_MODE", "unlimited_ocr_http").strip().lower()
    ocr_min_text_chars: int = int(os.getenv("OCR_MIN_TEXT_CHARS", "60"))
    unlimited_ocr_url: str = os.getenv("UNLIMITED_OCR_URL", "http://127.0.0.1:10000").rstrip("/")
    unlimited_ocr_model: str = os.getenv("UNLIMITED_OCR_MODEL", "Unlimited-OCR")
    unlimited_ocr_timeout_seconds: int = int(os.getenv("UNLIMITED_OCR_TIMEOUT_SECONDS", "1200"))

    # Local vision fallback in LM Studio. Use a VLM, not a text-only GGUF.
    lmstudio_vision_fallback: bool = _bool("LM_STUDIO_VISION_FALLBACK", True)
    lmstudio_url: str = os.getenv("LM_STUDIO_URL", "http://127.0.0.1:1234/v1").rstrip("/")
    lmstudio_vision_model: str = os.getenv(
        "LM_STUDIO_VISION_MODEL", "qwen3-vl-4b-instruct"
    )
    lmstudio_timeout_seconds: int = int(os.getenv("LM_STUDIO_TIMEOUT_SECONDS", "300"))

    # Structuring is independent from validation. "heuristic" needs no LLM.
    # For optional local structuring, point this OpenAI-compatible endpoint at LM Studio.
    structuring_mode: str = os.getenv("STRUCTURING_MODE", "heuristic")
    structuring_api_url: str = os.getenv("STRUCTURING_API_URL", "http://127.0.0.1:1234/v1").rstrip("/")
    structuring_api_key: str = os.getenv("STRUCTURING_API_KEY", "")
    structuring_model: str = os.getenv("STRUCTURING_MODEL", "qwen3-4b-instruct-2507")
    structuring_timeout_seconds: int = int(os.getenv("STRUCTURING_TIMEOUT_SECONDS", "120"))

    # Local vision fallback via direct GGUF (llama-cpp-python)
    gguf_vision_fallback: bool = _bool("GGUF_VISION_FALLBACK", True)
    gguf_model_path: Path = Path(os.getenv("GGUF_MODEL_PATH", "Qwen3-VL-4B-Instruct-GGUF/Qwen3-VL-4B-Instruct-Q4_K_M.gguf"))
    gguf_mmproj_path: Path = Path(os.getenv("GGUF_MMPROJ_PATH", "Qwen3-VL-4B-Instruct-GGUF/mmproj-Qwen3-VL-4B-Instruct-F16.gguf"))
    gguf_n_ctx: int = int(os.getenv("GGUF_N_CTX", "2048"))
    gguf_n_gpu_layers: int = int(os.getenv("GGUF_N_GPU_LAYERS", "0"))

    # RapidOCR fallback / engine
    rapidocr_fallback: bool = _bool("RAPIDOCR_FALLBACK", True)

    input_cost_per_1m: float = float(os.getenv("INPUT_COST_PER_1M", "0"))
    output_cost_per_1m: float = float(os.getenv("OUTPUT_COST_PER_1M", "0"))
    allow_configured_buyer_checksum_exception: bool = _bool(
        "ALLOW_CONFIGURED_BUYER_CHECKSUM_EXCEPTION", True
    )


settings = Settings()
