from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean

from .models import ProcessResult

ID_FIELDS = ["vendor_gstin", "buyer_gstin", "invoice_number", "po_number"]
DATE_FIELDS = ["invoice_date"]
MONEY_FIELDS = ["cgst", "sgst", "igst", "round_off", "grand_total"]
TEXT_FIELDS = ["vendor_name", "place_of_supply"]


def _norm(value) -> str:
    return " ".join(str(value or "").lower().split())


def _norm_id(value) -> str:
    return "".join(str(value or "").upper().split())


def _money(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value * 100)) if not value.is_integer() else int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_ground_truth(path: Path) -> dict[str, dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        if "documents" in raw and isinstance(raw["documents"], list):
            raw = raw["documents"]
        else:
            return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    if isinstance(raw, list):
        out = {}
        for item in raw:
            name = item.get("document") or item.get("filename") or item.get("file")
            if name:
                out[str(name)] = item
        return out
    raise ValueError("Unsupported ground_truth.json shape")


def _align_lines(pred: list[dict], truth: list[dict]) -> list[tuple[dict, dict]]:
    """Greedy alignment: prioritize HSN equality, then description similarity."""
    remaining = set(range(len(truth)))
    pairs: list[tuple[dict, dict]] = []
    for p in pred:
        best_i, best_score = None, -1.0
        for i in remaining:
            t = truth[i]
            score = 2.0 if _norm_id(p.get("hsn")) == _norm_id(t.get("hsn")) else 0.0
            score += SequenceMatcher(None, _norm(p.get("description")), _norm(t.get("description"))).ratio()
            if score > best_score:
                best_i, best_score = i, score
        if best_i is not None:
            pairs.append((p, truth[best_i]))
            remaining.remove(best_i)
    return pairs


@dataclass
class EvalSummary:
    field_accuracy: dict[str, float]
    line_item_accuracy: float
    decision_accuracy: float | None
    false_auto_approves: int
    avg_cost_usd: float
    avg_latency_ms: float
    documents: int

    def to_markdown(self) -> str:
        rows = ["| Metric | Result |", "|---|---:|"]
        for field, score in self.field_accuracy.items():
            rows.append(f"| Field accuracy — {field} | {score:.1%} |")
        rows += [
            f"| Line-item accuracy | {self.line_item_accuracy:.1%} |",
            f"| Decision accuracy | {'n/a' if self.decision_accuracy is None else f'{self.decision_accuracy:.1%}'} |",
            f"| False AUTO_APPROVEs | {self.false_auto_approves} |",
            f"| Avg cost/document | ${self.avg_cost_usd:.6f} |",
            f"| Avg latency/document | {self.avg_latency_ms:.0f} ms |",
            f"| Documents | {self.documents} |",
        ]
        return "\n".join(rows)


def evaluate(results: list[ProcessResult], ground_truth_path: Path) -> EvalSummary:
    truth = _load_ground_truth(ground_truth_path)
    counters: dict[str, list[bool]] = {f: [] for f in ID_FIELDS + DATE_FIELDS + MONEY_FIELDS + TEXT_FIELDS + ["is_invoice"]}
    line_checks: list[bool] = []
    decision_checks: list[bool] = []
    false_auto = 0

    for result in results:
        expected = truth.get(result.document)
        if not expected:
            continue
        pred = result.extraction.model_dump(mode="json")
        for field in ID_FIELDS:
            if field in expected:
                counters[field].append(_norm_id(pred.get(field)) == _norm_id(expected.get(field)))
        for field in DATE_FIELDS:
            if field in expected:
                counters[field].append(str(pred.get(field) or "") == str(expected.get(field) or ""))
        for field in MONEY_FIELDS:
            if field in expected:
                a, b = _money(pred.get(field)), _money(expected.get(field))
                counters[field].append(a is not None and b is not None and abs(a - b) <= 100)
        for field in TEXT_FIELDS:
            if field in expected:
                counters[field].append(_norm(pred.get(field)) == _norm(expected.get(field)))
        if "is_invoice" in expected:
            counters["is_invoice"].append(bool(pred.get("is_invoice")) == bool(expected.get("is_invoice")))

        pred_lines = pred.get("line_items", [])
        truth_lines = expected.get("line_items", [])
        if truth_lines:
            pairs = _align_lines(pred_lines, truth_lines)
            for p, t in pairs:
                checks = [
                    _norm_id(p.get("hsn")) == _norm_id(t.get("hsn")),
                    abs(float(p.get("qty", 0)) - float(t.get("qty", 0))) < 1e-9,
                    abs((_money(p.get("rate")) or 0) - (_money(t.get("rate")) or 0)) <= 100,
                    abs((_money(p.get("taxable_value")) or 0) - (_money(t.get("taxable_value")) or 0)) <= 100,
                    abs(float(p.get("gst_rate", 0)) - float(t.get("gst_rate", 0))) < 1e-9,
                ]
                line_checks.extend(checks)
            missing = max(0, len(truth_lines) - len(pairs))
            line_checks.extend([False] * (missing * 5))

        expected_decision = expected.get("decision")
        if expected_decision:
            correct = result.decision == expected_decision
            decision_checks.append(correct)
            if result.decision == "AUTO_APPROVE" and expected_decision != "AUTO_APPROVE":
                false_auto += 1

    field_accuracy = {k: mean(v) for k, v in counters.items() if v}
    return EvalSummary(
        field_accuracy=field_accuracy,
        line_item_accuracy=mean(line_checks) if line_checks else 0.0,
        decision_accuracy=mean(decision_checks) if decision_checks else None,
        false_auto_approves=false_auto,
        avg_cost_usd=mean([r.metrics.cost_estimate_usd for r in results]) if results else 0.0,
        avg_latency_ms=mean([r.metrics.latency_ms for r in results]) if results else 0.0,
        documents=len(results),
    )
