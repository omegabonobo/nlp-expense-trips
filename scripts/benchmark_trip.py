#!/usr/bin/env python3
"""Benchmark Basic and Best extraction on an isolated trip copy.

The command intentionally starts each quality run from a fresh line-item state
so one run cannot inherit descriptions, amounts, or review choices from the
other. It writes diagnostic workbooks and a JSON comparison inside the
selected trip; it never changes the source documents.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from nlp_expenses.extraction.statements import parse_all_statements
from nlp_expenses.generator import extract_trip_expenses
from nlp_expenses.lifecycle import record_generated_workbook
from nlp_expenses.line_items import (
    LINE_ITEM_REVIEW_FILE,
    apply_line_item_review,
    line_item_review_view,
    save_line_item_review,
)
from nlp_expenses.trip_metadata import apply_trip_metadata_defaults
from nlp_expenses.trips import trip_mode, trip_receipts_dir, trip_statements_dir
from nlp_expenses.workbook import build_workbook


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare Basic/offline and Best/OpenAI extraction on an isolated IVADO trip."
    )
    parser.add_argument("trip", type=Path, help="Isolated trip folder to benchmark.")
    parser.add_argument(
        "--quality",
        action="append",
        choices=["basic", "best"],
        dest="qualities",
        help="Quality to run; repeat for both. Defaults to basic and best.",
    )
    args = parser.parse_args()

    root = Path.cwd().resolve()
    trip = args.trip.resolve()
    if trip.parent != root / "trips" or not trip.is_dir():
        raise SystemExit(
            "Benchmark trip must be an existing folder directly under this project's trips/."
        )
    if trip_mode(trip) != "ivado":
        raise SystemExit("This benchmark command currently supports IVADO trips only.")
    qualities = args.qualities or ["basic", "best"]
    run_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results: dict[str, dict] = {}

    for quality in qualities:
        state_path = trip / LINE_ITEM_REVIEW_FILE
        state_path.unlink(missing_ok=True)
        results[quality] = run_quality(root, trip, quality, run_stamp)

    report = {
        "trip": trip.name,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": (
            "One-pass receipt extraction, fresh line-item review, IVADO statement matching, "
            "and diagnostic workbook creation. No manual ground-truth labels were supplied."
        ),
        "results": results,
        "comparison": compare_results(results),
    }
    report_path = unique_path(trip / f"benchmark_{trip.name}_{run_stamp}.json")
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Benchmark report: {report_path}")


def run_quality(root: Path, trip: Path, quality: str, run_stamp: str) -> dict:
    llm_mode = "required" if quality == "best" else "off"
    warnings: list[str] = []
    started = time.perf_counter()

    def progress(update) -> None:
        print(f"[{quality}] {update.message}", flush=True)

    try:
        expenses = extract_trip_expenses(
            trip_receipts_dir(trip),
            root,
            "ivado",
            llm_mode,
            progress_callback=progress,
            warning_callback=warnings.append,
            allow_openai_prompt=False,
        )
    except Exception as exc:
        return {
            "status": "error",
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "error": str(exc),
            "warnings": warnings,
        }

    apply_trip_metadata_defaults(trip, expenses)
    save_line_item_review(trip, expenses, llm_mode=llm_mode)
    review = line_item_review_view(trip)
    snapshot_path = unique_path(trip / f"benchmark_{run_stamp}_{quality}_line_items.json")
    snapshot_path.write_text(json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    apply_line_item_review(trip, expenses, require_fresh=True)

    transactions = parse_all_statements(trip_statements_dir(trip))
    output = unique_path(
        trip / f"expense_review_{trip.name}_ivado_{run_stamp}_{quality}-benchmark.xlsx"
    )
    build_workbook(trip, expenses, transactions, output_path=output)
    openai_success_count = sum(
        1
        for row in review["receipts"]
        if "structured with openai" in str(row.get("review_note") or "").lower()
    )
    fallback_only = quality == "best" and openai_success_count == 0
    if fallback_only:
        fallback_output = unique_path(
            output.with_name(output.name.replace("_best-benchmark", "_best-fallback-benchmark"))
        )
        output.replace(fallback_output)
        output = fallback_output
    record_generated_workbook(root, trip, output)

    receipt_rows = review["receipts"]
    lines = [line for receipt in receipt_rows for line in receipt["line_items"]]
    meaningful_lines = [
        line for line in lines if line.get("description") != "Alcohol adjustment - manual"
    ]
    matched = [row for row in transactions if row.expense_id]
    suggested = [row for row in transactions if row.suggested_expense_id]
    return {
        "status": "fallback_only"
        if fallback_only
        else ("complete_with_warnings" if warnings else "complete"),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "workbook": output.name,
        "workbook_bytes": output.stat().st_size,
        "line_item_snapshot": snapshot_path.name,
        "warnings": warnings,
        "receipt_count": len(receipt_rows),
        "date_coverage": count_present(receipt_rows, "date"),
        "vendor_coverage": sum(
            1
            for row in receipt_rows
            if row.get("vendor")
            and str(row["vendor"]).lower() not in {"unknown", "unknown supplier"}
        ),
        "total_coverage": count_number(receipt_rows, "receipt_total"),
        "currency_coverage": count_present(receipt_rows, "currency"),
        "meal_receipt_count": review["summary"]["meal_receipt_count"],
        "line_count": len(meaningful_lines),
        "synthetic_line_count": sum(1 for line in meaningful_lines if line.get("synthetic")),
        "alcohol_line_count": review["summary"]["alcohol_count"],
        "excluded_line_count": review["summary"]["excluded_count"],
        "meal_receipts_reconciled": sum(
            1
            for row in receipt_rows
            if str(row.get("expense_type") or "").startswith("meal") and row.get("reconciled")
        ),
        "meal_receipts_needing_review": review["summary"]["review_count"],
        "ui_blocking_receipt_count": review["summary"]["blocking_count"],
        "openai_success_receipt_count": openai_success_count,
        "local_fallback_receipt_count": len(warnings),
        "statement_transaction_count": len(transactions),
        "statement_auto_match_count": len(matched),
        "statement_suggestion_count": len(suggested),
        "receipts": {
            row["source_file"]: {
                "date": row.get("date"),
                "vendor": row.get("vendor"),
                "expense_type": row.get("expense_type"),
                "receipt_total": row.get("receipt_total"),
                "currency": row.get("currency"),
                "line_count": sum(
                    1
                    for line in row["line_items"]
                    if line.get("description") != "Alcohol adjustment - manual"
                ),
                "alcohol_lines": [
                    line.get("description") for line in row["line_items"] if line.get("is_alcohol")
                ],
                "reconciled": row.get("reconciled"),
                "status": row.get("status"),
            }
            for row in receipt_rows
        },
    }


def compare_results(results: dict[str, dict]) -> dict:
    basic = results.get("basic", {})
    best = results.get("best", {})
    if not basic or not best:
        return {
            "available": False,
            "reason": "Run both Basic and Best quality in the same benchmark before comparing fields.",
        }
    if basic.get("status") == "error" or best.get("status") == "error":
        return {
            "available": False,
            "reason": "Both runs must complete before fields can be compared.",
        }
    if best.get("openai_success_receipt_count", 0) == 0:
        return {
            "available": False,
            "reason": (
                "Best quality did not successfully process any receipt with OpenAI; "
                "the output is a local-fallback diagnostic, not a quality comparison."
            ),
        }
    basic_receipts = basic.get("receipts", {})
    best_receipts = best.get("receipts", {})
    fields = [
        "date",
        "vendor",
        "expense_type",
        "receipt_total",
        "currency",
        "line_count",
        "alcohol_lines",
    ]
    differences = {}
    for field in fields:
        changed = [
            name
            for name in sorted(set(basic_receipts) | set(best_receipts))
            if basic_receipts.get(name, {}).get(field) != best_receipts.get(name, {}).get(field)
        ]
        differences[field] = {"count": len(changed), "receipts": changed}
    return {"available": True, "field_differences": differences}


def count_present(rows: list[dict], field: str) -> int:
    return sum(1 for row in rows if row.get(field) not in (None, ""))


def count_number(rows: list[dict], field: str) -> int:
    return sum(1 for row in rows if isinstance(row.get(field), (int, float)))


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10_000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not select a unique benchmark output beside {path.name}.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
