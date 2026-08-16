from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from nlp_expenses.models import Expense
from nlp_expenses.reconciliation import statement_coverage_view
from nlp_expenses.trip_metadata import (
    apply_trip_metadata_defaults,
    required_metadata_gaps,
    save_policy_exception,
    save_trip_metadata,
    trip_metadata,
    trip_policy_warnings,
)
from nlp_expenses.trips import ensure_trip
from nlp_expenses.workbook import build_arvine_workbook


class TripMetadataTests(unittest.TestCase):
    def complete_metadata(self) -> dict:
        return {
            "claim_program": "arvine_only",
            "traveller": "Florent",
            "company": "Arvine Inc.",
            "start_date": "2026-07-01",
            "end_date": "2026-07-03",
            "origins": ["Montreal"],
            "destinations": ["New York"],
            "business_purpose": "Client planning",
            "client_project": "Project Atlas",
            "cost_centre": "CONSULTING",
            "approver": "Reviewer",
            "payment_method": "Corporate card",
            "default_paid_by": "arvine_corporate_bmo",
            "payer_confirmed": True,
            "policy_profile": "standard",
            "expected_accounts": ["AMEX 1234"],
            "policy": {
                "receipt_required_threshold": 25,
                "allowed_categories": ["flight", "hotel", "transport", "meal", "other"],
                "meal_limit_cad": 75,
                "alcohol_treatment": "review",
                "personal_expense_treatment": "review",
                "mileage_rate_cad": 0.7,
                "per_diem_cad": 90,
                "statement_coverage_buffer_days": 0,
            },
        }

    def test_metadata_validation_persistence_and_required_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_new-york", mode="arvine")
            self.assertIn("traveller", required_metadata_gaps(trip))
            saved = save_trip_metadata(trip, self.complete_metadata())
            self.assertEqual(saved["destinations"], ["New York"])
            self.assertEqual(saved["expected_accounts"], ["AMEX 1234"])
            self.assertEqual(saved["claim_program"], "company_reimbursed")
            self.assertEqual(saved["default_paid_by"], "company_card")
            self.assertEqual(required_metadata_gaps(trip), [])
            with self.assertRaisesRegex(ValueError, "before"):
                save_trip_metadata(
                    trip,
                    {
                        **self.complete_metadata(),
                        "start_date": "2026-07-04",
                        "end_date": "2026-07-03",
                    },
                )

    def test_trip_purpose_inherits_to_invoice_and_workbook_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_new-york", mode="arvine")
            save_trip_metadata(trip, self.complete_metadata())
            receipt = trip / "expenses_receipts" / "hotel.pdf"
            receipt.write_bytes(b"fixture")
            expense = Expense(
                source_file=receipt,
                expense_id="EXP-1",
                date="2026-07-01",
                supplier_name="Hotel",
                expense_type="hotel",
                amount=100,
                currency="CAD",
            )
            apply_trip_metadata_defaults(trip, [expense])
            self.assertEqual(expense.business_purpose, "Client planning")
            workbook_path = build_arvine_workbook(trip, [expense], [])
            workbook = load_workbook(workbook_path, data_only=False)
            self.assertEqual(workbook["expense_detail"]["AD2"].value, "Client planning")
            summary = workbook["expense_summary"]
            self.assertEqual(summary["B4"].value, "Client planning / Project Atlas")
            self.assertEqual(summary["W2"].value, "Florent")
            self.assertEqual(summary["W8"].value, "Client planning")

    def test_statement_coverage_uses_trip_dates_and_buffer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_new-york", mode="arvine")
            save_trip_metadata(trip, self.complete_metadata())
            state = {
                "transactions": [
                    {
                        "provider": "amex",
                        "account_label": "1234",
                        "transaction_date": "2026-07-02",
                        "source_rows": [{"file": "amex.csv", "row": 2}],
                        "match_eligible": True,
                        "ignored": False,
                        "possible_duplicate": False,
                    }
                ]
            }
            coverage = statement_coverage_view(trip, state)
            self.assertEqual(coverage["expected_start"], "2026-07-01")
            self.assertEqual(coverage["expected_end"], "2026-07-03")
            self.assertEqual(
                {gap["code"] for gap in coverage["gaps"]},
                {"coverage_starts_late", "coverage_ends_early"},
            )

    def test_policy_warnings_require_a_documented_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_new-york", mode="arvine")
            metadata = self.complete_metadata()
            metadata["policy"]["allowed_categories"] = ["hotel", "meal"]
            save_trip_metadata(trip, metadata)
            expenses = [
                {
                    "source_file": "dinner.pdf",
                    "expense_type": "meal",
                    "cad_amount_used": 120.0,
                },
                {
                    "source_file": "taxi.pdf",
                    "expense_type": "transport",
                    "cad_amount_used": 25.0,
                },
            ]
            warnings = trip_policy_warnings(trip, expenses, [])
            self.assertEqual(len(warnings), 2)
            self.assertFalse(any(warning["resolved"] for warning in warnings))
            meal_warning = next(
                warning for warning in warnings if warning["id"].startswith("meal_limit:")
            )
            save_policy_exception(trip, meal_warning["id"], "Client dinner approved by manager.")
            refreshed = trip_policy_warnings(trip, expenses, [])
            self.assertTrue(
                next(item for item in refreshed if item["id"] == meal_warning["id"])["resolved"]
            )
            self.assertEqual(
                trip_metadata(trip)["policy_exceptions"][meal_warning["id"]],
                "Client dinner approved by manager.",
            )


if __name__ == "__main__":
    unittest.main()
