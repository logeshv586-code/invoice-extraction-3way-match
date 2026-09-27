from __future__ import annotations

import argparse
from pathlib import Path

from .evaluation import evaluate
from .pipeline import InvoicePipeline

SUPPORTED = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def _documents(folder: Path):
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED)


def cmd_process(args) -> int:
    result = InvoicePipeline().process(args.document)
    print(result.model_dump_json(indent=2))
    return 0


def cmd_batch(args) -> int:
    folder = Path(args.folder)
    out = Path("out/results.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    pipeline = InvoicePipeline()
    results = [pipeline.process(path) for path in _documents(folder)]
    with out.open("w", encoding="utf-8") as fh:
        for result in results:
            fh.write(result.model_dump_json() + "\n")
    print(f"Processed {len(results)} documents -> {out}")
    return 0


def cmd_eval(args) -> int:
    folder = Path(args.folder)
    gt = folder / "ground_truth.json"
    if not gt.exists():
        raise SystemExit(f"Ground truth not found: {gt}")
    pipeline = InvoicePipeline()
    results = [pipeline.process(path) for path in _documents(folder)]
    summary = evaluate(results, gt)
    print(summary.to_markdown())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m extractor")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("process", help="Process one invoice/document")
    p.add_argument("document")
    p.set_defaults(func=cmd_process)

    b = sub.add_parser("batch", help="Process all supported documents in a folder")
    b.add_argument("folder")
    b.set_defaults(func=cmd_batch)

    e = sub.add_parser("eval", help="Process folder and compare with ground_truth.json")
    e.add_argument("folder")
    e.set_defaults(func=cmd_eval)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
