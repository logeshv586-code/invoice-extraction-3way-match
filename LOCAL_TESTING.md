# Local Testing Guide

## Recommended local model

For the OCR-failure fallback, load a **vision-capable** model in LM Studio. A text-only T5 or ordinary text GGUF cannot read invoice images.

Recommended default:

```text
Qwen3-VL 4B Instruct (GGUF)
```

A smaller or larger LM Studio VLM can be substituted by changing `LM_STUDIO_VISION_MODEL`.

## 1. Clone and install

```powershell
git clone https://github.com/logeshv586-code/invoice-extraction-3way-match.git
cd invoice-extraction-3way-match
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

## 2. Assessment database

Keep:

```env
DB_BACKEND=sqlite
ERP_DB=samples/erp.db
```

Optional PostgreSQL later:

```powershell
pip install -r requirements-postgres.txt
```

Then:

```env
DB_BACKEND=postgres
POSTGRES_DSN=postgresql://user:password@localhost:5432/invoice_demo
```

The PostgreSQL tables must use the same schema as the assessment SQLite database.

## 3. Start Unlimited-OCR

Keep it on the configured endpoint:

```env
OCR_MODE=unlimited_ocr_http
UNLIMITED_OCR_URL=http://127.0.0.1:10000
```

Text PDFs do not need OCR; embedded text is used automatically.

## 4. Start LM Studio fallback

In LM Studio:

1. Download/load a vision-capable GGUF such as Qwen3-VL 4B Instruct.
2. Open **Developer**.
3. Start the local server.
4. Confirm the server is listening on port `1234`.

Configure:

```env
LM_STUDIO_VISION_FALLBACK=true
LM_STUDIO_URL=http://127.0.0.1:1234/v1
LM_STUDIO_VISION_MODEL=<exact model id shown by LM Studio>
```

The fallback is called only when primary visual OCR errors or returns too little usable text.

## 5. Choose structuring mode

### No LLM after OCR

```env
STRUCTURING_MODE=heuristic
```

All accounting validation is still available. The heuristic parser is intentionally conservative, so difficult invoices may go to `NEEDS_REVIEW`.

### Optional local LM Studio structuring

```env
STRUCTURING_MODE=lmstudio
STRUCTURING_API_URL=http://127.0.0.1:1234/v1
STRUCTURING_MODEL=<loaded text or vision model id>
```

This model only turns OCR text into schema JSON. It does not approve invoices.

## 6. Test one invoice

```powershell
python -m extractor process samples\inv_07.pdf
```

## 7. Test one invoice supplied as multiple image pages

Use the page order:

```powershell
python -m extractor process samples\inv_07_page1.jpg samples\inv_07_page2.jpg samples\inv_07_page3.jpg
```

The pages are treated as **one invoice**. OCR evidence is retained page-by-page. Conflicting non-buyer GSTINs across pages force `NEEDS_REVIEW`.

## 8. Test rules without any LLM/OCR server

```powershell
pytest
```

GST, arithmetic, duplicate detection, PO/GRN matching, previously billed quantity, rate tolerance, HSN/GST matching and decisions are deterministic Python.

## 9. Run assessment evaluation

After copying the supplied `erp.db`, `ground_truth.json`, and invoice samples:

```powershell
python -m extractor eval samples\
```

Do not invent README metrics. Copy the real evaluation output into the final README before submission.
