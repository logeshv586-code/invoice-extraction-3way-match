from pathlib import Path

import requests

from extractor.config import Settings
from extractor.ocr import DocumentReader


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self.text}}]}


def settings_for() -> Settings:
    return Settings(
        buyer_gstin="29AAACR5055K1Z3",
        ocr_mode="unlimited_ocr_http",
        ocr_min_text_chars=20,
        lmstudio_vision_fallback=True,
        unlimited_ocr_url="http://127.0.0.1:10000",
        lmstudio_url="http://127.0.0.1:1234/v1",
        lmstudio_vision_model="qwen3-vl-4b-instruct",
    )


def test_lmstudio_vision_is_used_when_unlimited_ocr_fails(tmp_path, monkeypatch):
    image = tmp_path / "invoice.jpg"
    image.write_bytes(b"not-a-real-image-but-enough-for-request-building")

    def fake_post(url, **kwargs):
        if ":10000/" in url:
            raise requests.ConnectionError("primary OCR unavailable")
        assert ":1234/" in url
        return FakeResponse(
            "TAX INVOICE Vendor GSTIN 27AAACR5055K1Z7 "
            "Invoice INV-100 PO PO-1 Grand Total 1180.00"
        )

    monkeypatch.setattr(requests, "post", fake_post)

    result = DocumentReader(settings_for()).read(image)

    assert result.provider == "lmstudio/qwen3-vl-4b-instruct"
    assert "INV-100" in result.text


def test_multiple_images_preserve_page_evidence_and_flag_vendor_gstin_conflict(tmp_path, monkeypatch):
    p1 = tmp_path / "page1.jpg"
    p2 = tmp_path / "page2.jpg"
    p1.write_bytes(b"page1")
    p2.write_bytes(b"page2")

    responses = iter(
        [
            "TAX INVOICE buyer 29AAACR5055K1Z3 vendor 27AAACR5055K1Z7 invoice INV-100 total 1000.00",
            "TAX INVOICE buyer 29AAACR5055K1Z3 vendor 33AAACR5055K1Z1 invoice INV-100 total 1000.00",
        ]
    )

    def fake_post(url, **kwargs):
        assert ":10000/" in url
        return FakeResponse(next(responses))

    monkeypatch.setattr(requests, "post", fake_post)

    result = DocumentReader(settings_for()).read_many([p1, p2])

    assert len(result.segments) == 2
    assert {segment.source for segment in result.segments} == {"page1.jpg", "page2.jpg"}
    assert result.warnings
    assert result.warnings[0][0] == "CROSS_PAGE_VENDOR_GSTIN_CONFLICT"


def test_gguf_vision_is_used_when_unlimited_and_lmstudio_fail(tmp_path, monkeypatch):
    image = tmp_path / "invoice.jpg"
    image.write_bytes(b"dummy")

    def fake_post(url, **kwargs):
        raise requests.ConnectionError("server unavailable")

    monkeypatch.setattr(requests, "post", fake_post)

    # Mock _direct_gguf_vision on DocumentReader
    monkeypatch.setattr(
        DocumentReader,
        "_direct_gguf_vision",
        lambda self, images: "TAX INVOICE Vendor GSTIN 27AAACR5055K1Z7 Invoice INV-100 PO PO-1 Grand Total 1180.00",
    )

    settings = Settings(
        ocr_mode="unlimited_ocr_http",
        ocr_min_text_chars=20,
        lmstudio_vision_fallback=True,
        gguf_vision_fallback=True,
    )
    result = DocumentReader(settings).read(image)
    assert result.provider == "gguf/qwen3-vl-4b-instruct"
    assert "INV-100" in result.text


def test_rapidocr_is_used_when_vision_models_fail(tmp_path, monkeypatch):
    image = tmp_path / "invoice.jpg"
    image.write_bytes(b"dummy")

    def fake_post(url, **kwargs):
        raise requests.ConnectionError("server unavailable")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(
        DocumentReader,
        "_rapid_ocr",
        lambda self, img: "TAX INVOICE Vendor GSTIN 27AAACR5055K1Z7 Invoice INV-100 PO PO-1 Grand Total 1180.00",
    )

    settings = Settings(
        ocr_mode="unlimited_ocr_http",
        ocr_min_text_chars=20,
        lmstudio_vision_fallback=False,
        gguf_vision_fallback=False,
        rapidocr_fallback=True,
    )
    result = DocumentReader(settings).read(image)
    assert result.provider == "rapidocr"
    assert "INV-100" in result.text

