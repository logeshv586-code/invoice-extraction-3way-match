# AI Usage

AI coding assistance was used to accelerate scaffolding, review the assessment requirements, draft tests, and reason about edge cases. The implementation is intentionally split so AI/OCR is used only for document extraction/structuring; approval logic is deterministic Python and can be tested without an AI model.

Before the live session I will be able to explain and modify every module without AI assistance. In particular I reviewed the GSTIN checksum, GST slab-date boundary, invoice-number normalization, Indian financial-year duplicate rule, quantity/rate checks, and PO/GRN matching logic.

## Extraction providers

1. Text-native PDFs use PyMuPDF without an LLM.
2. Scans/photos use `baidu/Unlimited-OCR` as the primary OCR path.
3. If primary OCR fails or returns insufficient usable text, local fallback automatically transcribes the image using:
   - Direct in-process GGUF VLM (`Qwen3-VL 4B Instruct` via `llama-cpp-python`).
   - Local LM Studio vision endpoint (`http://127.0.0.1:1234/v1`).
   - RapidOCR in-process ONNX engine.
4. OCR text can be structured with deterministic heuristics (no LLM), direct local GGUF (`STRUCTURING_MODE=gguf`), or an OpenAI-compatible model endpoint.

The model repositories are not vendored into this project.

## Safety boundary

Document text is treated as untrusted data. Instructions printed inside an invoice are never executed. Models may only transcribe/extract fields. GST rules, arithmetic, duplicate detection, PO/GRN checks, previously billed quantities, confidence gating and the final decision are deterministic Python.

If OCR, a local model, schema parsing, or a database call fails, the system fails closed to `NEEDS_REVIEW`; model output alone can never produce an approval.
