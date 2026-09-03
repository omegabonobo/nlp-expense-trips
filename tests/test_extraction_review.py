from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nlp_expenses.extraction_benchmark import (
    compare_benchmark_qualities,
    evaluate_benchmark_quality,
    load_benchmark_set,
)
from nlp_expenses.line_items import (
    acknowledge_extraction_changes,
    line_item_review_view,
    save_line_item_review,
    set_expense_review,
    set_line_item_review,
)
from nlp_expenses.models import Expense, LineItem
from nlp_expenses.trips import ensure_trip

ROOT = Path(__file__).parents[1]


def confident_expense(path: Path, *, vendor: str = "Test Cafe") -> Expense:
    return Expense(
        source_file=path,
        expense_id="TEST-1",
        date="2026-08-10",
        supplier_name=vendor,
        expense_type="meal",
        amount=20.0,
        currency="CAD",
        confidence=0.95,
        line_items=[
            LineItem(
                description="Lunch",
                amount=20.0,
                confidence=0.94,
                confidence_reason="Clear printed description and amount.",
            )
        ],
    )


class ExceptionDrivenReviewTests(unittest.TestCase):
    def test_complete_high_confidence_receipt_has_no_exceptions(self):
        with tempfile.TemporaryDirectory() as temporary:
            trip = ensure_trip(Path(temporary), "202608_ready", mode="company")
            receipt = trip / "expenses_receipts" / "ready.pdf"
            receipt.write_bytes(b"fixture")
            save_line_item_review(trip, [confident_expense(receipt)])

            view = line_item_review_view(trip)
            reviewed = view["receipts"][0]
            self.assertEqual(reviewed["exception_status"], "ready")
            self.assertEqual(view["exceptions"], [])
            self.assertEqual(reviewed["field_evidence"]["vendor"]["confidence"], 0.95)
            self.assertTrue(reviewed["field_evidence"]["vendor"]["reason"])
            self.assertEqual(
                reviewed["line_items"][0]["confidence_reason"],
                "Clear printed description and amount.",
            )

            ready = set_expense_review(trip, receipt.name, {"reviewed": True})
            self.assertEqual(ready["receipts"][0]["status"], "ready")

    def test_exception_queue_deduplicates_missing_low_confidence_and_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            trip = ensure_trip(Path(temporary), "202608_exceptions", mode="ivado")
            first_path = trip / "expenses_receipts" / "first.pdf"
            second_path = trip / "expenses_receipts" / "second.pdf"
            first_path.write_bytes(b"first")
            second_path.write_bytes(b"second")
            first = confident_expense(first_path)
            first.currency = None
            first.line_items[0].is_alcohol = True
            first.line_items[0].alcohol_confidence = 0.60
            first.line_items[0].alcohol_reason = "Possible beverage keyword"
            second = confident_expense(second_path)
            save_line_item_review(trip, [first, second])

            view = line_item_review_view(trip)
            categories = [exception["category"] for exception in view["exceptions"]]
            self.assertIn("missing_field", categories)
            self.assertIn("uncertain_alcohol", categories)
            self.assertNotIn("likely_duplicate", categories)
            self.assertEqual(
                len({item["id"] for item in view["exceptions"]}), len(view["exceptions"])
            )
            self.assertGreater(view["summary"]["exception_blocking_count"], 0)

            first.currency = "CAD"
            save_line_item_review(trip, [first, second])
            refreshed = line_item_review_view(trip)
            duplicates = [
                item for item in refreshed["exceptions"] if item["category"] == "likely_duplicate"
            ]
            self.assertEqual(len(duplicates), 1)

    def test_rescan_preserves_correction_and_exposes_acknowledgeable_diff(self):
        with tempfile.TemporaryDirectory() as temporary:
            trip = ensure_trip(Path(temporary), "202608_diff", mode="company")
            receipt = trip / "expenses_receipts" / "receipt.pdf"
            receipt.write_bytes(b"unchanged source")
            save_line_item_review(trip, [confident_expense(receipt, vendor="Automatic Cafe")])
            corrected = set_expense_review(
                trip,
                receipt.name,
                {"vendor": "Reviewed Cafe", "reviewed": True},
            )
            line = corrected["receipts"][0]["line_items"][0]
            set_line_item_review(trip, receipt.name, line["line_id"], {"reviewed": True})

            rescanned = confident_expense(receipt, vendor="Automatic Cafe Renamed")
            rescanned.line_items[0].description = "Lunch item"
            save_line_item_review(trip, [rescanned])
            view = line_item_review_view(trip)
            reviewed = view["receipts"][0]
            self.assertEqual(reviewed["vendor"], "Reviewed Cafe")
            self.assertTrue(reviewed["reviewed"])
            self.assertTrue(reviewed["line_items"][0]["reviewed"])
            self.assertTrue(reviewed["extraction_changes"])
            vendor_change = next(
                change
                for change in reviewed["extraction_changes"]
                if change.get("field") == "vendor"
            )
            self.assertTrue(vendor_change["preserved_user_value"])
            self.assertIn(
                "extraction_change",
                {exception["category"] for exception in view["exceptions"]},
            )

            acknowledged = acknowledge_extraction_changes(trip, receipt.name)
            receipt_view = acknowledged["receipts"][0]
            self.assertTrue(receipt_view["extraction_changes"])
            self.assertTrue(receipt_view["changes_acknowledged_at"])
            self.assertNotIn(
                "extraction_change",
                {exception["category"] for exception in acknowledged["exceptions"]},
            )


class ExtractionBenchmarkTests(unittest.TestCase):
    def test_synthetic_benchmark_reports_required_release_metrics(self):
        dataset = load_benchmark_set(ROOT / "benchmarks" / "receipt-extraction-v1.json")
        candidates = {}
        for case in dataset["cases"]:
            expected = dict(case["expected"])
            expected["line_items"] = [{"description": "Total", "amount": expected["amount"]}]
            expected["exceptions"] = []
            candidates[case["id"]] = expected
        metrics = evaluate_benchmark_quality(
            dataset,
            candidates,
            runtime_seconds=1.25,
            extractor_version="receipt-test-v1",
        )
        self.assertEqual(metrics["field_coverage"], 1.0)
        self.assertEqual(metrics["field_accuracy"], 1.0)
        self.assertEqual(metrics["line_total_agreement"], 1.0)
        self.assertEqual(metrics["blocking_count"], 0)
        self.assertEqual(metrics["review_count"], 0)
        self.assertEqual(metrics["runtime_seconds"], 1.25)
        self.assertEqual(metrics["extractor_version"], "receipt-test-v1")

        comparison = compare_benchmark_qualities({"basic": metrics, "best": metrics})
        self.assertTrue(comparison["available"])
        self.assertEqual(comparison["best_minus_basic"]["field_accuracy"], 0.0)
