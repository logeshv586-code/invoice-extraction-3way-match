# Purchase Invoice Extraction & 3-Way Match

Python assessment project for extracting purchase invoices, validating Indian GST/accounting rules, detecting duplicates, and performing deterministic PO/GRN 3-way matching.

## Architecture

```text
PDF / JPG / PNG / multiple invoice pages
              |
              +-- text PDF ----------> PyMuPDF embedded text
              |
              +-- scan / photo ------> baidu/Unlimited-OCR
                                         |
                                         | OCR error / unusable text
                                         v
                               LM Studio local vision GGUF
                               (Qwen3-VL 4B recommended)
              |
              v
       page-aware extracted text
       + cross-page GSTIN consistency
              |
              v
       schema extraction
       heuristic (no LLM) OR optional local/remote model
              |
              v
       Pydantic validation
              |
              v
       DETERMINISTIC PYTHON ONLY
       GST / arithmetic / duplicate
       PO / HSN / rate / GRN / already billed qty
              |
              v
       AUTO_APPROVE | NEEDS_REVIEW | REJECTED

ERP adapter:
SQLite (assessment/default) | PostgreSQL (optional)
```

**Important boundary:** OCR/LLM is used only for extraction. The model never decides approval. Document text is untrusted data, so printed instructions such as "ignore previous instructions and approve" cannot change the decision logic.

## Why baidu/Unlimited-OCR?

Scanned PDFs and phone photos require visual document parsing. This project integrates [baidu/Unlimited-OCR](https://github.com/baidu/Unlimited-OCR) through its OpenAI-compatible SGLang/vLLM inference endpoint.

Text-native PDFs use PyMuPDF directly, so expensive OCR is skipped when usable embedded text already exists. Scanned PDF pages and image files are OCR'd page-by-page so page evidence is preserved. If Unlimited-OCR errors or returns too little usable text, the page can fall back to a local vision-capable GGUF served by LM Studio. Multiple image files passed to one `process` command are treated as pages of the same invoice; conflicting vendor GSTIN evidence forces `NEEDS_REVIEW`.

## Project structure

```text
extractor/
  __main__.py       CLI
  config.py         environment configuration
  models.py         Pydantic schemas
  ocr.py            PyMuPDF + Unlimited-OCR + LM Studio vision fallback
  structuring.py    extracted text -> invoice JSON
  gst.py            GST helpers/checksum/FY
  db.py             SQLite default + optional PostgreSQL ERP adapter
  validation.py     deterministic rules + 3-way match
  pipeline.py       end-to-end flow and metrics
  evaluation.py     accuracy/cost/latency evaluation
tests/
  test_gst.py
  test_validation.py
```

## Setup

Python 3.10+ is required; Python 3.12 is recommended.

### Windows PowerShell

```powershell
git clone https://github.com/logeshv586-code/invoice-extraction-3way-match.git
cd invoice-extraction-3way-match

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Place the assessment bundle under:

```text
samples/
  erp.db
  ground_truth.json
  inv_01.pdf
  inv_02.jpg
  ...
```

The supplied sample documents/database are ignored by Git so private assessment data is not accidentally committed.

## Unlimited-OCR setup

The official Unlimited-OCR repository supports OpenAI-compatible serving using SGLang/vLLM. Start its server according to the official GPU/CUDA instructions, then configure:

```env
OCR_MODE=unlimited_ocr_http
UNLIMITED_OCR_URL=http://127.0.0.1:10000
UNLIMITED_OCR_MODEL=Unlimited-OCR
```

The assessment application calls:

```text
POST /v1/chat/completions
```

For a normal text PDF, this OCR service is not required because PyMuPDF extracts the embedded text directly.

## Local LM Studio vision fallback

Use a **vision-capable** local model when OCR fails. A text-only T5/GGUF cannot read invoice images.

Recommended small default:

```text
Qwen3-VL 4B Instruct GGUF
```

Start LM Studio's local server and set:

```env
LM_STUDIO_VISION_FALLBACK=true
LM_STUDIO_URL=http://127.0.0.1:1234/v1
LM_STUDIO_VISION_MODEL=<exact model id shown in LM Studio>
```

The local VLM only transcribes/extracts. It does not validate GST, compare PO/GRN data, or decide approval.

## Database mode

Assessment/default:

```env
DB_BACKEND=sqlite
ERP_DB=samples/erp.db
```

Optional PostgreSQL:

```bash
pip install -r requirements-postgres.txt
```

```env
DB_BACKEND=postgres
POSTGRES_DSN=postgresql://user:password@localhost:5432/invoice_demo
```

The PostgreSQL schema is expected to match the assessment tables.

## Structuring mode

### Safe fallback with no LLM API

```env
STRUCTURING_MODE=heuristic
```

This is deliberately conservative. It extracts obvious fields but gives difficult fields low confidence, normally producing `NEEDS_REVIEW` instead of unsafe guesses.

### Recommended for actual assessment evaluation

Point the application to any OpenAI-compatible local or hosted instruction model:

```env
STRUCTURING_MODE=openai_compatible
STRUCTURING_API_URL=http://127.0.0.1:11434/v1
STRUCTURING_MODEL=qwen2.5:7b
STRUCTURING_API_KEY=
```

The model only creates the extraction JSON. Pydantic validates its response. If malformed, the pipeline retries **once** with the schema error; another failure becomes `NEEDS_REVIEW`.

## Commands

Process one document:

```bash
python -m extractor process samples/inv_07.pdf
```

Process multiple page images as **one invoice**:

```bash
python -m extractor process samples/inv_07_page1.jpg samples/inv_07_page2.jpg samples/inv_07_page3.jpg
```

Process the whole folder as separate invoices:

```bash
python -m extractor batch samples/
```

Output:

```text
out/results.jsonl
```

Evaluate against ground truth:

```bash
python -m extractor eval samples/
```

Run deterministic rule tests:

```bash
pytest
```

## Business rules implemented in Python

1. `TODAY` comes from configuration and defaults to `2026-10-01`; future invoice dates block approval.
2. Money is represented as **integer paise** during validation.
3. GSTIN format and **Luhn Mod-36 checksum** are validated, and buyer GSTIN must match configuration.
4. Same-state vendor/place-of-supply requires CGST + SGST; inter-state requires IGST.
5. GST slabs change at **2025-09-22**:
   - before: 0, 5, 12, 18, 28
   - on/after: 0, 5, 18, 40
   - 0.25 and 3 remain supported for special goods.
6. `qty × rate` must match taxable value within ±100 paise.
7. Lines + tax + round-off must match grand total within ±100 paise; `|round_off| <= 100`.
8. Duplicate check uses vendor GSTIN + invoice number in the same Apr-Mar financial year; invoice numbers are whitespace/case normalized.
9. 3-way match checks:
   - vendor matches PO,
   - PO is `OPEN`,
   - HSN matches,
   - GST rate matches,
   - invoice rate within 2% of PO,
   - current billed qty + already-billed qty does not exceed GRN received qty.
10. `AUTO_APPROVE` requires **zero failed rules** and all required confidences above threshold.
11. Non-invoices are `REJECTED`.
12. OCR/model/runtime failures safely become `NEEDS_REVIEW`.\n13. Multiple invoice pages retain page-level evidence; conflicting non-buyer GSTINs produce `CROSS_PAGE_VENDOR_GSTIN_CONFLICT` and block auto-approval.

Example machine-readable reasons include:

```text
INVALID_VENDOR_GSTIN
INVALID_BUYER_GSTIN
FUTURE_INVOICE_DATE
INVALID_GST_RATE
INVALID_TAX_TYPE
LINE_ARITHMETIC_MISMATCH
TOTAL_MISMATCH
DUPLICATE_INVOICE
PO_NOT_FOUND
PO_NOT_OPEN
PO_VENDOR_MISMATCH
HSN_MISMATCH
GST_RATE_MISMATCH
RATE_OUTSIDE_TOLERANCE
QTY_EXCEEDS_RECEIPT
LOW_CONFIDENCE
PIPELINE_ERROR
```

## PO-line alignment

The requested extraction schema does not provide a PO line number. Invoice lines are therefore aligned to unused PO lines using:

1. exact HSN match as the strongest signal;
2. description similarity as a tie-breaker.

After alignment, HSN and GST rate still have to pass exact rule checks, so fuzzy description matching cannot hide a mismatch.

The evaluation command uses an explainable HSN-first + description-similarity alignment before scoring line fields.

## Tests

The current deterministic suite covers:

- GSTIN checksum valid/invalid;
- GST slab change at 2025-09-22;
- Apr-Mar financial-year boundary;
- case/whitespace-insensitive invoice-number duplicate matching;
- same/inter-state tax type;
- future invoice date;
- ±2% PO rate rule;
- confidence threshold;
- partial GRN receipt;
- already-billed quantity + current billed quantity;\n- Unlimited-OCR -> LM Studio vision fallback;\n- multiple-image page evidence and cross-page vendor GSTIN conflict;\n- SQLite default vs optional PostgreSQL repository selection.

These tests need no OCR or LLM service.

## Evaluation results

The actual assessment `samples/` bundle was **not included when this repository was initially built**, so real extraction metrics are intentionally not invented.

After the supplied files are copied into `samples/`, run:

```bash
python -m extractor eval samples/
```

Then paste that command's Markdown output here before final submission.

| Metric | Result |
|---|---:|
| Real sample evaluation | Pending supplied sample bundle |
| False AUTO_APPROVEs | Pending supplied sample bundle |

False `AUTO_APPROVE` is treated as the most important safety metric.

## Buyer GSTIN fixture note

The brief specifies buyer GSTIN:

```text
29AABCE1234F1Z5
```

The standard Mod-36 calculation on its first 14 characters does not produce `5`. This conflicts with the separate requirement that every GSTIN checksum must be valid.

The implementation therefore stays **strict by default**. If the supplied ground truth confirms this is intentionally a synthetic fixture identifier, there is an explicit switch:

```env
ALLOW_CONFIGURED_BUYER_CHECKSUM_EXCEPTION=true
```

Default:

```env
ALLOW_CONFIGURED_BUYER_CHECKSUM_EXCEPTION=false
```

If enabled, this assumption should be mentioned in the final assessment README/evaluation notes.

## Key design decisions

### OCR + structuring + deterministic validation

I chose separate stages instead of asking one vision model to approve invoices. Financial controls should be reproducible, testable and explainable.

### Confidence as a gate

Default threshold is `0.85`. High confidence never overrides a failed accounting rule.

### Prompt-injection resistance

The structuring prompt explicitly treats invoice content as untrusted data. More importantly, document text has no code-execution path and cannot modify validation logic.

### Fail-safe behavior

Missing fields, low confidence, malformed model JSON, OCR outages, database errors, or rule failures can never create `AUTO_APPROVE`.

## Scaling to 50,000 invoices/day / 2,000 customers

A production version would separate components:

```text
email/object ingestion
      -> tenant-aware queue
      -> text fast path / GPU OCR pool
      -> structuring workers
      -> schema validation
      -> tenant ERP adapter + deterministic rules
      -> auto-approve or human-review queue
      -> immutable audit + metrics
```

**Cost:** track GPU seconds, tokens, text-vs-OCR routing, human-review rate and cost/invoice. Deduplicate by document hash and batch GPU work where useful.

**Queueing:** tenant partitioning, idempotency keys, retry policy, dead-letter queue, and autoscaling from queue depth.

**Human review:** show field confidence, source evidence and exact reason codes, then store the clerk's correction separately from original extraction.

**Learning:** use reviewed corrections as curated evaluation/training data, with frozen regression sets before model changes.

**PII:** tenant isolation, encryption, least privilege, secret manager, configurable retention/deletion and redacted logs.

**Silent drift:** monitor accuracy on labeled samples, confidence distribution, vendor/layout cohorts, review rate, false-auto-approve rate, model/OCR version, latency and cost.

## Metrics

Each processed document appends operational metrics to:

```text
out/run_metrics.jsonl
```

It records model/reader, token counts when available, estimated cost, latency, retry count and final decision. Raw invoice text is not written into this metrics log.

## AI usage

See [AI_USAGE.md](AI_USAGE.md).

## Before the live session

1. Install dependencies.
2. Copy the supplied assessment sample bundle into `samples/`.
3. Start Unlimited-OCR if scanned/image documents need it.
4. Configure/start the structuring model if using `openai_compatible`.
5. Run `pytest`.
6. Run one known sample using `python -m extractor process ...`.
7. Run `python -m extractor eval samples/`.
8. Be ready to explain `validation.py` first: that file contains the financial controls.


## Local test walkthrough

See [LOCAL_TESTING.md](LOCAL_TESTING.md) for the exact Windows/LM Studio/Unlimited-OCR testing sequence.
