# AI Usage

AI coding assistance was used to accelerate scaffolding, review the assessment requirements, draft tests, and reason about edge cases. The implementation is intentionally split so AI/OCR is used only for document extraction/structuring; approval logic is deterministic Python and can be tested without an AI model.

Before the live session I will be able to explain and modify every module without AI assistance. In particular I reviewed the GSTIN checksum, GST slab-date boundary, invoice-number normalization, Indian financial-year duplicate rule, quantity/rate checks, and PO/GRN matching logic.

## External model used

The OCR integration targets `baidu/Unlimited-OCR` through its OpenAI-compatible inference endpoint. The model repository is not vendored into this project.

## Safety boundary

Document text is treated as untrusted data. Instructions printed inside an invoice are never executed. The structuring prompt explicitly prohibits following document instructions, and the final decision is never delegated to the model.
