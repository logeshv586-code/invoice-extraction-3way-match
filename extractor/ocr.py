from __future__ import annotations

import base64
import mimetypes
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fitz
import requests

from .config import Settings


@dataclass
class OCRResult:
    text: str
    provider: str


class DocumentReader:
    def __init__(self, settings: Settings):
        self.settings = settings

    def read(self, path: str | Path) -> OCRResult:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            embedded = self._extract_pdf_text(path)
            if len("".join(embedded.split())) >= 120:
                return OCRResult(embedded, "pymupdf-text")
            return OCRResult(self._unlimited_ocr(self._pdf_to_images(path)), "baidu/Unlimited-OCR")
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
            return OCRResult(self._unlimited_ocr([path]), "baidu/Unlimited-OCR")
        raise ValueError(f"Unsupported document type: {suffix}")

    @staticmethod
    def _extract_pdf_text(path: Path) -> str:
        with fitz.open(path) as doc:
            return "\n\n".join(page.get_text("text") for page in doc).strip()

    @staticmethod
    def _pdf_to_images(path: Path, dpi: int = 300) -> list[Path]:
        tmp = Path(tempfile.mkdtemp(prefix="invoice_ocr_"))
        images: list[Path] = []
        with fitz.open(path) as doc:
            matrix = fitz.Matrix(dpi / 72, dpi / 72)
            for i, page in enumerate(doc, start=1):
                out = tmp / f"page_{i:04d}.png"
                page.get_pixmap(matrix=matrix, alpha=False).save(out)
                images.append(out)
        return images

    @staticmethod
    def _image_part(path: Path) -> dict:
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}

    def _unlimited_ocr(self, images: list[Path]) -> str:
        if self.settings.ocr_mode != "unlimited_ocr_http":
            raise RuntimeError(f"Unsupported OCR_MODE={self.settings.ocr_mode!r}")
        if not images:
            raise RuntimeError("No pages supplied to OCR")

        multi = len(images) > 1
        prompt = "Multi page parsing." if multi else "document parsing."
        content = [{"type": "text", "text": prompt}] + [self._image_part(p) for p in images]
        payload = {
            "model": self.settings.unlimited_ocr_model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "stream": False,
            "skip_special_tokens": False,
            "images_config": {"image_mode": "base" if multi else "gundam"},
            "custom_params": {"ngram_size": 35, "window_size": 1024 if multi else 128},
        }
        try:
            response = requests.post(
                f"{self.settings.unlimited_ocr_url}/v1/chat/completions",
                json=payload,
                timeout=self.settings.unlimited_ocr_timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                "Unlimited-OCR service is unavailable. Start the SGLang/vLLM server "
                "or point UNLIMITED_OCR_URL at a compatible endpoint."
            ) from exc
        body = response.json()
        try:
            return body["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Unexpected Unlimited-OCR response shape") from exc
