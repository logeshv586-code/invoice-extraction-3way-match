from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .config import Settings, settings as default_settings
from .db import build_erp_repository
from .models import InvoiceExtraction, ProcessResult, Reason, RunMetrics
from .pipeline import InvoicePipeline

app = FastAPI(
    title="Effortless Invoice Extraction & 3-Way Match Backend",
    version="1.0.0",
    description="Unified backend server supporting GGUF vision VLM, RapidOCR, and deterministic 3-way matching.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TextProcessRequest(BaseModel):
    text: str
    document_name: str = "pasted_text_invoice.txt"


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: list[dict]
    temperature: Optional[float] = 0.0
    max_tokens: Optional[int] = 1024


@app.get("/health", response_class=JSONResponse)
@app.get("/api/status", response_class=JSONResponse)
def get_status():
    settings = default_settings
    db_ok = False
    vendor_count = 0
    po_count = 0
    try:
        repo = build_erp_repository(settings)
        if hasattr(repo, "connect"):
            with repo.connect() as conn:
                vendor_count = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
                po_count = conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0]
                db_ok = True
    except Exception:
        pass

    gguf_ready = Path(settings.gguf_model_path).exists() and Path(settings.gguf_mmproj_path).exists()
    rapidocr_ready = False
    try:
        import rapidocr  # noqa: F401
        rapidocr_ready = True
    except ImportError:
        pass

    return {
        "status": "online",
        "port": 8010,
        "today": str(settings.today),
        "buyer_gstin": settings.buyer_gstin,
        "database": {
            "backend": settings.db_backend,
            "connected": db_ok,
            "path": str(settings.erp_db),
            "vendors_count": vendor_count,
            "purchase_orders_count": po_count,
        },
        "models": {
            "gguf_model": str(settings.gguf_model_path),
            "gguf_ready": gguf_ready,
            "rapidocr_ready": rapidocr_ready,
            "default_ocr_mode": settings.ocr_mode,
            "structuring_mode": settings.structuring_mode,
        },
    }


@app.post("/api/process", response_model=ProcessResult)
async def process_invoice(files: List[UploadFile] = File(...)):
    """Upload one or multiple pages of an invoice (PDF, JPG, PNG)."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    temp_dir = Path(tempfile.mkdtemp(prefix="api_upload_"))
    saved_paths: list[Path] = []
    try:
        for f in files:
            dest = temp_dir / f.filename
            with dest.open("wb") as buffer:
                shutil.copyfileobj(f.file, buffer)
            saved_paths.append(dest)

        pipeline = InvoicePipeline()
        result = pipeline.process(saved_paths)
        return result
    finally:
        # cleanup temp uploaded files
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


@app.post("/api/process-text", response_model=ProcessResult)
def process_invoice_text(payload: TextProcessRequest):
    """Process pasted/raw invoice text directly through structuring and 3-way match validation."""
    pipeline = InvoicePipeline()
    started = time.perf_counter()

    try:
        structured = pipeline.structurer.structure(payload.text)
        extraction = structured.extraction
        reasons = validate_invoice_reasons(extraction, pipeline.db, pipeline.settings)

        if not extraction.is_invoice:
            decision = "REJECTED"
        elif reasons:
            decision = "NEEDS_REVIEW"
        else:
            decision = "AUTO_APPROVE"

        metrics = RunMetrics(
            reader="raw-text-input",
            structuring_model=structured.model,
            tokens_in=structured.tokens_in,
            tokens_out=structured.tokens_out,
            latency_ms=int((time.perf_counter() - started) * 1000),
            retries=structured.retries,
        )
    except Exception as exc:
        extraction = InvoiceExtraction(is_invoice=True, confidence={})
        reasons = [Reason(code="PIPELINE_ERROR", message=str(exc))]
        decision = "NEEDS_REVIEW"
        metrics = RunMetrics(
            reader="raw-text-input",
            structuring_model="error",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    return ProcessResult(
        document=payload.document_name,
        extraction=extraction,
        decision=decision,
        reasons=reasons,
        metrics=metrics,
    )


def validate_invoice_reasons(extraction, db, settings):
    from .validation import validate_invoice
    return validate_invoice(extraction, db, settings)


@app.post("/v1/chat/completions")
def openai_compatible_chat(payload: ChatCompletionRequest):
    """OpenAI-compatible chat completion endpoint supporting vision transcription and structuring."""
    pipeline = InvoicePipeline()
    # Check if there are image_url parts in user messages
    images: list[Path] = []
    text_prompt = ""
    temp_dir = Path(tempfile.mkdtemp(prefix="api_v1_"))

    try:
        for msg in payload.messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                text_prompt += " " + content
            elif isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        text_prompt += " " + part.get("text", "")
                    elif part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if url.startswith("data:image/"):
                            header, b64_data = url.split(";base64,", 1)
                            ext = header.split("/")[1]
                            tmp_img = temp_dir / f"img_{len(images)}.{ext}"
                            tmp_img.write_bytes(base64.b64decode(b64_data))
                            images.append(tmp_img)

        if images:
            # Run visual page reader (cascades GGUF -> RapidOCR)
            ocr_result = pipeline.reader.read_many(images)
            res_content = ocr_result.text
        else:
            # Text structuring request
            structured = pipeline.structurer.structure(text_prompt)
            res_content = structured.extraction.model_dump_json()

        return {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": res_content,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Effortless – Invoice Extraction & 3-Way Match</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --success-bg: #064e3b;
      --success-text: #34d399;
      --warning-bg: #78350f;
      --warning-text: #fbbf24;
      --danger-bg: #7f1d1d;
      --danger-text: #f87171;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'Inter', -apple-system, sans-serif;
      padding: 24px;
      line-height: 1.5;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
    }
    .logo-title h1 { font-size: 1.35rem; font-weight: 700; color: #fff; }
    .logo-title p { font-size: 0.85rem; color: var(--text-muted); }
    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 0.75rem;
      background: rgba(59, 130, 246, 0.15);
      border: 1px solid var(--primary);
      color: #93c5fd;
      padding: 4px 10px;
      border-radius: 9999px;
      font-family: 'JetBrains Mono', monospace;
    }
    .status-dot { width: 8px; height: 8px; border-radius: 50%; background: #34d399; }
    .grid { display: grid; grid-template-columns: 1fr 1.35fr; gap: 24px; }
    @media (max-width: 980px) { .grid { grid-template-columns: 1fr; } }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 20px;
    }
    .card h2 { font-size: 1rem; font-weight: 600; margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }
    .tabs { display: flex; gap: 8px; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
    .tab-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-weight: 500;
      padding: 6px 12px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 0.85rem;
    }
    .tab-btn.active { background: var(--border); color: #fff; }
    .drop-zone {
      border: 2px dashed var(--border);
      border-radius: 10px;
      padding: 32px 16px;
      text-align: center;
      cursor: pointer;
      transition: all 0.2s;
      background: rgba(15, 23, 42, 0.5);
    }
    .drop-zone:hover, .drop-zone.dragover { border-color: var(--primary); background: rgba(59, 130, 246, 0.05); }
    .file-input { display: none; }
    .file-info { margin-top: 10px; font-size: 0.8rem; color: #38bdf8; font-family: 'JetBrains Mono', monospace; }
    textarea {
      width: 100%;
      height: 180px;
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 8px;
      color: #f8fafc;
      padding: 12px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.82rem;
      resize: vertical;
    }
    .btn {
      background: var(--primary);
      color: #fff;
      border: none;
      padding: 10px 18px;
      border-radius: 8px;
      font-size: 0.9rem;
      font-weight: 600;
      cursor: pointer;
      width: 100%;
      margin-top: 12px;
      transition: background 0.15s;
    }
    .btn:hover { background: var(--primary-hover); }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .decision-banner {
      padding: 14px 18px;
      border-radius: 10px;
      font-weight: 700;
      font-size: 1.15rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
    }
    .decision-AUTO_APPROVE { background: var(--success-bg); color: var(--success-text); border: 1px solid #059669; }
    .decision-NEEDS_REVIEW { background: var(--warning-bg); color: var(--warning-text); border: 1px solid #d97706; }
    .decision-REJECTED { background: var(--danger-bg); color: var(--danger-text); border: 1px solid #dc2626; }
    .reasons-list { list-style: none; margin-bottom: 16px; }
    .reason-item {
      background: rgba(245, 158, 11, 0.1);
      border-left: 3px solid #fbbf24;
      padding: 8px 12px;
      border-radius: 4px;
      margin-bottom: 8px;
      font-size: 0.85rem;
    }
    .reason-code { font-weight: 700; font-family: 'JetBrains Mono', monospace; color: #fde68a; margin-right: 6px; }
    .fields-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;
      margin-bottom: 16px;
    }
    .field-card {
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px;
    }
    .field-label { font-size: 0.72rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; }
    .field-val { font-size: 0.92rem; font-weight: 600; color: #fff; font-family: 'JetBrains Mono', monospace; margin-top: 2px; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 0.82rem; }
    th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); }
    th { color: var(--text-muted); font-size: 0.72rem; text-transform: uppercase; }
    td { font-family: 'JetBrains Mono', monospace; }
    .empty-state { text-align: center; color: var(--text-muted); padding: 48px 16px; font-size: 0.9rem; }
    .json-box {
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.75rem;
      color: #94a3b8;
      max-height: 250px;
      overflow-y: auto;
      margin-top: 14px;
    }
    .spinner {
      display: inline-block;
      width: 16px;
      height: 16px;
      border: 2px solid rgba(255,255,255,0.3);
      border-radius: 50%;
      border-top-color: #fff;
      animation: spin 0.8s linear infinite;
      vertical-align: middle;
      margin-right: 8px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
  <div class="header">
    <div class="logo-title">
      <h1>Effortless Purchase Invoice Extraction & 3-Way Match</h1>
      <p>Local GGUF Vision (Qwen3-VL 4B) + RapidOCR + 3-Way ERP Matching Engine</p>
    </div>
    <div class="status-badge">
      <span class="status-dot"></span>
      <span id="backend-status">Backend :8010 Online</span>
    </div>
  </div>

  <div class="grid">
    <!-- Left Column: Input Form -->
    <div>
      <div class="card">
        <div class="tabs">
          <button class="tab-btn active" id="tab-file-btn" onclick="switchTab('file')">File Upload (PDF / Scans)</button>
          <button class="tab-btn" id="tab-text-btn" onclick="switchTab('text')">Direct Text Input</button>
        </div>

        <!-- File Tab -->
        <div id="tab-file">
          <div class="drop-zone" id="drop-zone" onclick="document.getElementById('file-input').click()">
            <svg style="width:36px;height:36px;margin:0 auto 8px;fill:#64748b" viewBox="0 0 24 24">
              <path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM14 13v4h-4v-4H7l5-5 5 5h-3z"/>
            </svg>
            <p style="font-weight:600;font-size:0.95rem">Click to browse or drop invoice files here</p>
            <p style="font-size:0.78rem;color:#94a3b8;margin-top:4px">Supports PDF, JPG, PNG (multi-page invoices supported)</p>
            <input type="file" id="file-input" class="file-input" multiple accept=".pdf,.png,.jpg,.jpeg,.webp" onchange="handleFiles(this.files)">
          </div>
          <div class="file-info" id="file-info"></div>
          <button class="btn" id="btn-process-file" onclick="processUploadedFiles()">Extract & Match Invoice</button>
        </div>

        <!-- Text Tab -->
        <div id="tab-text" style="display:none">
          <textarea id="raw-text-input" placeholder="Paste extracted or raw invoice text here...&#10;e.g.&#10;TAX INVOICE&#10;Vendor GSTIN: 27AAACR5055K1Z7&#10;Buyer GSTIN: 29AABCE1234F1Z5&#10;Invoice No: INV-100&#10;Invoice Date: 2026-09-15&#10;Place of Supply: 29&#10;PO Number: PO-101&#10;Item: Server Setup | 8471 | 2.0 | 5000.00 | 10000.00 | 18%&#10;Grand Total: 11800.00"></textarea>
          <button class="btn" id="btn-process-text" onclick="processRawText()">Process Text & Match</button>
        </div>
      </div>

      <!-- System Status Card -->
      <div class="card" style="font-size:0.8rem">
        <h2>Active Local Pipeline Engines</h2>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;color:#94a3b8">
          <div>Primary OCR: <span style="color:#fff">Unlimited-OCR (10000)</span></div>
          <div>VLM Fallback: <span style="color:#34d399">Qwen3-VL 4B (In-Process)</span></div>
          <div>Fast Engine: <span style="color:#34d399">RapidOCR ONNX</span></div>
          <div>ERP DB: <span style="color:#fff">SQLite (samples/erp.db)</span></div>
          <div>Today Date: <span style="color:#fff" id="stat-today">2026-10-01</span></div>
          <div>Buyer GSTIN: <span style="color:#fff" id="stat-buyer">29AABCE1234F1Z5</span></div>
        </div>
      </div>
    </div>

    <!-- Right Column: Results Panel -->
    <div>
      <div class="card" id="results-card">
        <h2>Extraction & 3-Way Match Decision</h2>
        <div id="empty-state" class="empty-state">
          Upload an invoice or paste text to see extraction, GST validation, and 3-way match audit.
        </div>

        <div id="results-content" style="display:none">
          <div id="decision-banner" class="decision-banner">
            <span id="decision-text">AUTO_APPROVE</span>
            <span id="metrics-badge" style="font-size:0.75rem;font-weight:400;opacity:0.9"></span>
          </div>

          <div id="reasons-container" style="display:none">
            <p style="font-size:0.78rem;font-weight:600;text-transform:uppercase;color:#fbbf24;margin-bottom:6px">Review Reasons</p>
            <ul class="reasons-list" id="reasons-list"></ul>
          </div>

          <div class="fields-grid">
            <div class="field-card"><div class="field-label">Invoice Number</div><div class="field-val" id="res-inv-no">-</div></div>
            <div class="field-card"><div class="field-label">Invoice Date</div><div class="field-val" id="res-inv-date">-</div></div>
            <div class="field-card"><div class="field-label">Vendor GSTIN</div><div class="field-val" id="res-vendor-gstin">-</div></div>
            <div class="field-card"><div class="field-label">Buyer GSTIN</div><div class="field-val" id="res-buyer-gstin">-</div></div>
            <div class="field-card"><div class="field-label">PO Number</div><div class="field-val" id="res-po-no">-</div></div>
            <div class="field-card"><div class="field-label">Grand Total</div><div class="field-val" id="res-grand-total">-</div></div>
          </div>

          <p style="font-size:0.78rem;font-weight:600;text-transform:uppercase;color:#94a3b8;margin-top:12px">Line Items</p>
          <div style="overflow-x:auto">
            <table>
              <thead>
                <tr>
                  <th>Description</th>
                  <th>HSN</th>
                  <th>Qty</th>
                  <th>Rate</th>
                  <th>Taxable</th>
                  <th>GST %</th>
                </tr>
              </thead>
              <tbody id="line-items-body"></tbody>
            </table>
          </div>

          <p style="font-size:0.78rem;font-weight:600;text-transform:uppercase;color:#94a3b8;margin-top:16px">Structured JSON Output</p>
          <pre class="json-box" id="json-raw"></pre>
        </div>
      </div>
    </div>
  </div>

  <script>
    let selectedFiles = [];

    function switchTab(tab) {
      document.getElementById('tab-file-btn').classList.toggle('active', tab === 'file');
      document.getElementById('tab-text-btn').classList.toggle('active', tab === 'text');
      document.getElementById('tab-file').style.display = tab === 'file' ? 'block' : 'none';
      document.getElementById('tab-text').style.display = tab === 'text' ? 'block' : 'none';
    }

    const dropZone = document.getElementById('drop-zone');
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('dragover');
      handleFiles(e.dataTransfer.files);
    });

    function handleFiles(files) {
      selectedFiles = Array.from(files);
      const info = document.getElementById('file-info');
      if (selectedFiles.length === 1) {
        info.textContent = `Selected: ${selectedFiles[0].name} (${(selectedFiles[0].size/1024).toFixed(1)} KB)`;
      } else if (selectedFiles.length > 1) {
        info.textContent = `Selected: ${selectedFiles.length} pages (${selectedFiles.map(f => f.name).join(', ')})`;
      } else {
        info.textContent = '';
      }
    }

    async function processUploadedFiles() {
      if (!selectedFiles.length) {
        alert("Please select or drop at least one invoice file.");
        return;
      }
      const btn = document.getElementById('btn-process-file');
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span>Processing with OCR / GGUF...';

      const formData = new FormData();
      selectedFiles.forEach(f => formData.append('files', f));

      try {
        const resp = await fetch('/api/process', { method: 'POST', body: formData });
        if (!resp.ok) throw new Error(await resp.text());
        const data = await resp.json();
        renderResult(data);
      } catch (err) {
        alert('Error: ' + err.message);
      } finally {
        btn.disabled = false;
        btn.textContent = 'Extract & Match Invoice';
      }
    }

    async function processRawText() {
      const text = document.getElementById('raw-text-input').value.trim();
      if (!text) {
        alert("Please paste invoice text first.");
        return;
      }
      const btn = document.getElementById('btn-process-text');
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span>Matching against ERP...';

      try {
        const resp = await fetch('/api/process-text', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: text })
        });
        if (!resp.ok) throw new Error(await resp.text());
        const data = await resp.json();
        renderResult(data);
      } catch (err) {
        alert('Error: ' + err.message);
      } finally {
        btn.disabled = false;
        btn.textContent = 'Process Text & Match';
      }
    }

    function renderResult(res) {
      document.getElementById('empty-state').style.display = 'none';
      document.getElementById('results-content').style.display = 'block';

      const banner = document.getElementById('decision-banner');
      banner.className = `decision-banner decision-${res.decision}`;
      document.getElementById('decision-text').textContent = res.decision.replace('_', ' ');

      const m = res.metrics || {};
      document.getElementById('metrics-badge').textContent = `Engine: ${m.reader || 'N/A'} | Latency: ${m.latency_ms || 0}ms | Cost: $${(m.cost_estimate_usd || 0).toFixed(6)}`;

      const ext = res.extraction || {};
      document.getElementById('res-inv-no').textContent = ext.invoice_number || 'None';
      document.getElementById('res-inv-date').textContent = ext.invoice_date || 'None';
      document.getElementById('res-vendor-gstin').textContent = ext.vendor_gstin || 'None';
      document.getElementById('res-buyer-gstin').textContent = ext.buyer_gstin || 'None';
      document.getElementById('res-po-no').textContent = ext.po_number || 'None';
      document.getElementById('res-grand-total').textContent = ext.grand_total ? `₹${(ext.grand_total/100).toFixed(2)}` : 'None';

      // Reasons
      const rContainer = document.getElementById('reasons-container');
      const rList = document.getElementById('reasons-list');
      rList.innerHTML = '';
      if (res.reasons && res.reasons.length > 0) {
        rContainer.style.display = 'block';
        res.reasons.forEach(r => {
          const li = document.createElement('li');
          li.className = 'reason-item';
          li.innerHTML = `<span class="reason-code">[${r.code}]</span>${r.message}`;
          rList.appendChild(li);
        });
      } else {
        rContainer.style.display = 'none';
      }

      // Line items
      const tbody = document.getElementById('line-items-body');
      tbody.innerHTML = '';
      if (ext.line_items && ext.line_items.length > 0) {
        ext.line_items.forEach(li => {
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td>${li.description}</td>
            <td>${li.hsn}</td>
            <td>${li.qty}</td>
            <td>₹${(li.rate/100).toFixed(2)}</td>
            <td>₹${(li.taxable_value/100).toFixed(2)}</td>
            <td>${li.gst_rate}%</td>
          `;
          tbody.appendChild(tr);
        });
      } else {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#64748b">No line items parsed</td></tr>';
      }

      document.getElementById('json-raw').textContent = JSON.stringify(res, null, 2);
    }

    // Load status
    fetch('/api/status').then(r => r.json()).then(d => {
      document.getElementById('stat-today').textContent = d.today;
      document.getElementById('stat-buyer').textContent = d.buyer_gstin;
    }).catch(() => {});
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index_page():
    return HTMLResponse(content=HTML_DASHBOARD)


def run_server(host: str = "0.0.0.0", port: int = 8010):
    import uvicorn
    uvicorn.run("extractor.server:app", host=host, port=port, reload=False)
