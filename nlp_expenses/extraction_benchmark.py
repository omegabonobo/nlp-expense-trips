from __future__ import annotations

import json
from pathlib import Path

from nlp_expenses.line_items import EXTRACTED_FIELD_LABELS

BENCHMARK_SCHEMA_VERSION = 1
BENCHMARK_FIELDS = tuple(EXTRACTED_FIELD_LABELS)


def load_benchmark_set(path: Path) -> dict:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if dataset.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        raise ValueError(f"Unsupported extraction benchmark schema in {path.name}.")
    if dataset.get("privacy") != "synthetic":
        raise ValueError("Extraction benchmark cases must be explicitly marked synthetic.")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Extraction benchmark must contain at least one case.")
    identifiers = [case.get("id") for case in cases if isinstance(case, dict)]
    if len(identifiers) != len(set(identifiers)) or any(not value for value in identifiers):
        raise ValueError("Extraction benchmark case IDs must be present and unique.")
    return dataset


def evaluate_benchmark_quality(
    dataset: dict,
    candidates: dict[str, dict],
    *,
    runtime_seconds: float,
    extractor_version: str,
) -> dict:
    """Calculate release-comparable extraction metrics from synthetic ground truth."""

    field_expected = 0
    field_present = 0
    field_correct = 0
    per_field = {field: {"expected": 0, "present": 0, "correct": 0} for field in BENCHMARK_FIELDS}
    line_total_agreement = 0
    blocking_count = 0
    review_count = 0
    case_results = {}
    for case in dataset["cases"]:
        case_id = case["id"]
        expected = case.get("expected", {})
        candidate = candidates.get(case_id, {})
        correct_fields = []
        for field in BENCHMARK_FIELDS:
            expected_value = expected.get(field)
            if expected_value in (None, ""):
                continue
            field_expected += 1
            per_field[field]["expected"] += 1
            candidate_value = candidate.get(field)
            if candidate_value not in (None, ""):
                field_present += 1
                per_field[field]["present"] += 1
            if benchmark_values_equal(candidate_value, expected_value):
                field_correct += 1
                per_field[field]["correct"] += 1
                correct_fields.append(field)
        candidate_lines = [
            line
            for line in candidate.get("line_items", [])
            if isinstance(line, dict) and not line.get("system_type")
        ]
        candidate_line_total = round(
            sum(
                float(line["amount"])
                for line in candidate_lines
                if isinstance(line.get("amount"), (int, float))
            ),
            2,
        )
        candidate_total = candidate.get("amount")
        lines_agree = (
            isinstance(candidate_total, (int, float))
            and abs(candidate_line_total - float(candidate_total)) <= 0.05
        )
        line_total_agreement += int(lines_agree)
        exceptions = candidate.get("exceptions", [])
        blocking_count += sum(item.get("severity") == "blocking" for item in exceptions)
        review_count += sum(item.get("severity") == "review" for item in exceptions)
        case_results[case_id] = {
            "correct_fields": correct_fields,
            "field_accuracy": round(len(correct_fields) / max(1, len(expected)), 4),
            "line_total_agreement": lines_agree,
            "blocking_count": sum(item.get("severity") == "blocking" for item in exceptions),
            "review_count": sum(item.get("severity") == "review" for item in exceptions),
        }
    case_count = len(dataset["cases"])
    return {
        "case_count": case_count,
        "field_coverage": round(field_present / max(1, field_expected), 4),
        "field_accuracy": round(field_correct / max(1, field_expected), 4),
        "line_total_agreement": round(line_total_agreement / max(1, case_count), 4),
        "blocking_count": blocking_count,
        "review_count": review_count,
        "runtime_seconds": round(float(runtime_seconds), 3),
        "extractor_version": extractor_version,
        "per_field": per_field,
        "cases": case_results,
    }


def compare_benchmark_qualities(results: dict[str, dict]) -> dict:
    basic = results.get("basic")
    best = results.get("best")
    if not basic or not best or basic.get("status") == "error" or best.get("status") == "error":
        return {"available": False}
    return {
        "available": True,
        "best_minus_basic": {
            metric: round(float(best[metric]) - float(basic[metric]), 4)
            for metric in ("field_coverage", "field_accuracy", "line_total_agreement")
        },
        "best_review_reduction": int(basic["review_count"]) - int(best["review_count"]),
        "best_blocking_reduction": int(basic["blocking_count"]) - int(best["blocking_count"]),
    }


def benchmark_values_equal(candidate: object, expected: object) -> bool:
    if isinstance(candidate, (int, float)) and isinstance(expected, (int, float)):
        return abs(float(candidate) - float(expected)) <= 0.01
    return (
        " ".join(str(candidate or "").split()).casefold()
        == " ".join(str(expected or "").split()).casefold()
    )
