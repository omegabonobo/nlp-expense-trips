#!/usr/bin/env python3
"""Compare Basic and Best receipt extraction on the synthetic R018 benchmark."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from datetime import datetime
from pathlib import Path

from nlp_expenses import __version__
from nlp_expenses.extraction_benchmark import (
    compare_benchmark_qualities,
    evaluate_benchmark_quality,
    load_benchmark_set,
)
from nlp_expenses.generator import extract_trip_expenses
from nlp_expenses.line_items import line_item_review_view, save_line_item_review
from nlp_expenses.trips import ensure_trip, trip_receipts_dir


def main() -> None:
    project_root = Path(__file__).parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=project_root / "benchmarks" / "receipt-extraction-v1.json",
    )
    parser.add_argument(
        "--quality",
        action="append",
        choices=["basic", "best"],
        dest="qualities",
        help="Quality to run; repeat to compare both. Defaults to Basic and Best.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    dataset = load_benchmark_set(args.dataset.resolve())
    results = {}
    for quality in args.qualities or ["basic", "best"]:
        try:
            results[quality] = run_quality(dataset, quality)
        except Exception as failure:
            results[quality] = {"status": "error", "error": str(failure)}
    report = {
        "benchmark_id": dataset["benchmark_id"],
        "schema_version": dataset["schema_version"],
        "privacy": dataset["privacy"],
        "app_version": __version__,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "results": results,
        "comparison": compare_benchmark_qualities(results),
    }
    output = args.output or (
        project_root
        / "benchmark-results"
        / f"receipt-extraction-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Extraction benchmark report: {output}")
    if all(result.get("status") == "error" for result in results.values()):
        raise SystemExit(1)


def run_quality(dataset: dict, quality: str) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"nlp-expenses-{quality}-benchmark-") as temporary:
        root = Path(temporary)
        trip = ensure_trip(root, "202608_synthetic-benchmark", mode="ivado")
        receipts = trip_receipts_dir(trip)
        for case in dataset["cases"]:
            create_text_pdf(receipts / f"{case['id']}.pdf", case["source_text"])
        warnings = []
        started = time.perf_counter()
        expenses = extract_trip_expenses(
            receipts,
            root,
            "ivado",
            "required" if quality == "best" else "off",
            warning_callback=warnings.append,
            allow_openai_prompt=False,
        )
        save_line_item_review(
            trip,
            expenses,
            llm_mode="required" if quality == "best" else "off",
        )
        view = line_item_review_view(trip)
        runtime = time.perf_counter() - started
        candidates = {Path(receipt["source_file"]).stem: receipt for receipt in view["receipts"]}
        extractor_versions = sorted(
            {str(receipt.get("extractor_version") or "unknown") for receipt in view["receipts"]}
        )
        metrics = evaluate_benchmark_quality(
            dataset,
            candidates,
            runtime_seconds=runtime,
            extractor_version=", ".join(extractor_versions),
        )
        metrics.update(
            {
                "status": "complete_with_warnings" if warnings else "complete",
                "quality": quality,
                "warnings": warnings,
            }
        )
        return metrics


def create_text_pdf(path: Path, source_text: str) -> None:
    import fitz

    document = fitz.open()
    page = document.new_page(width=420, height=600)
    page.insert_textbox(
        fitz.Rect(36, 36, 384, 564),
        source_text,
        fontsize=12,
        fontname="helv",
        lineheight=1.4,
    )
    document.save(path)
    document.close()


if __name__ == "__main__":
    main()
