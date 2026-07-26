from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

from nlp_expenses.consolidation import (
    calculate_expense_result,
    consolidation_view,
    finalize_consolidation,
)
from nlp_expenses.generator import generate_review
from nlp_expenses.line_items import (
    add_line_item,
    line_item_review_view,
    remove_line_item,
    save_line_item_review,
    set_expense_review,
    set_line_item_review,
)
from nlp_expenses.models import Expense, LineItem
from nlp_expenses.reconciliation import (
    set_manual_match,
    set_transaction_decision,
    sync_reconciliation,
)
from nlp_expenses.trip_metadata import save_trip_metadata
from nlp_expenses.trips import ensure_trip


def complete_metadata(trip: Path) -> None:
    save_trip_metadata(
        trip,
        {
            "traveller": "Florent",
            "company": "Example Corp.",
            "start_date": "2026-07-01",
            "end_date": "2026-07-02",
            "business_purpose": "Client workshop",
            "approver": "Manager",
            "payment_method": "Personal card reimbursement",
        },
    )


def meal_expense(receipt: Path, currency: str = "CAD", amount: float = 120.0) -> Expense:
    return Expense(
        source_file=receipt,
        expense_id="",
        date="2026-07-01",
        supplier_name="Bistro",
        expense_type="meal",
        amount=amount,
        currency=currency,
        line_items=[
            LineItem(description="Dinner", amount=100.0),
            LineItem(
                description="Shiraz",
                amount=20.0,
                is_alcohol=True,
                alcohol_confidence=0.97,
                alcohol_reason="recognized wine",
            ),
        ],
    )


class ConsolidationTests(unittest.TestCase):
    def test_app_review_controls_people_lines_totals_and_finalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_app-first", mode="ivado")
            complete_metadata(trip)
            receipt = trip / "expenses_receipts" / "meal.pdf"
            receipt.write_bytes(b"fixture")
            save_line_item_review(trip, [meal_expense(receipt)])

            set_expense_review(
                trip,
                receipt.name,
                {"number_of_people": 2, "included": True, "business_purpose": "Client dinner"},
            )
            result = consolidation_view(root, trip)
            self.assertTrue(result["is_ready"])
            self.assertAlmostEqual(result["expenses"][0]["claimable_cad"], 50.0)
            self.assertEqual(result["expenses"][0]["number_of_people"], 2)

            finalized = finalize_consolidation(root, trip)
            self.assertTrue(finalized["current"])
            cocktail = next(
                item
                for item in line_item_review_view(trip)["receipts"][0]["line_items"]
                if item["description"] == "Shiraz"
            )
            set_line_item_review(trip, receipt.name, cocktail["line_id"], {"included": True})
            changed = consolidation_view(root, trip)
            self.assertFalse(changed["is_finalized"])
            self.assertAlmostEqual(changed["summary"]["claimable_cad"], 60.0)

    def test_manual_line_add_remove_and_automatic_reset_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_lines", mode="arvine")
            receipt = trip / "expenses_receipts" / "meal.pdf"
            receipt.write_bytes(b"fixture")
            save_line_item_review(trip, [meal_expense(receipt)])

            added = add_line_item(trip, receipt.name, "Tip", 15, included=True)
            manual = next(
                item
                for item in added["receipts"][0]["line_items"]
                if item["description"] == "Tip"
            )
            self.assertTrue(manual["manual"])
            removed = remove_line_item(trip, receipt.name, manual["line_id"])
            self.assertNotIn(
                "Tip",
                [item["description"] for item in removed["receipts"][0]["line_items"]],
            )

    def test_ivado_sync_mapping_fx_preview_and_workbook_reuse_the_same_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_ivado-fx", mode="ivado")
            complete_metadata(trip)
            receipt = trip / "expenses_receipts" / "bistro.pdf"
            receipt.write_bytes(b"fixture")
            statement = trip / "card_statements" / "card.csv"
            statement.write_text(
                "Date,Description,Amount,Foreign Spend Amount\n"
                "2026-07-01,BISTRO,75,50 AUD\n",
                encoding="utf-8",
            )
            expense = meal_expense(receipt, currency="AUD", amount=50)
            expense.line_items = [LineItem(description="Dinner", amount=50)]
            save_line_item_review(trip, [expense])

            with patch("nlp_expenses.generator.parse_receipt", return_value=expense):
                reconciliation = sync_reconciliation(trip, root, llm_mode="off")
            transaction = reconciliation["transactions"][0]
            self.assertEqual(transaction["expense_file"], receipt.name)
            preview = consolidation_view(root, trip)
            self.assertEqual(preview["expenses"][0]["cad_amount_used"], 75.0)
            self.assertEqual(preview["expenses"][0]["fx_rate"], 1.5)
            self.assertTrue(preview["is_ready"])

            set_manual_match(
                trip,
                transaction["group_id"],
                expense_file=receipt.name,
                use_auto=False,
            )
            with patch("nlp_expenses.generator.parse_receipt", return_value=expense):
                workbook_path = generate_review(trip, root, llm_mode="off")
            workbook = load_workbook(workbook_path, data_only=False)
            self.assertEqual(workbook["card_statements"]["D2"].value, "20260701_#1")
            self.assertEqual(workbook["expense_list"]["I2"].value, 1)
            self.assertEqual(workbook["expense_list"]["Q2"].value, True)

            set_manual_match(
                trip,
                transaction["group_id"],
                expense_file=None,
                use_auto=False,
            )
            unresolved = consolidation_view(root, trip)
            self.assertGreater(unresolved["summary"]["blocking_count"], 0)
            self.assertTrue(any(issue["kind"] == "statement_mapping" for issue in unresolved["issues"]))

    def test_statement_purchase_amount_prevents_double_dividing_a_shared_meal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_shared-meal", mode="ivado")
            complete_metadata(trip)
            receipt = trip / "expenses_receipts" / "shared.pdf"
            receipt.write_bytes(b"fixture")
            statement = trip / "card_statements" / "card.csv"
            statement.write_text(
                "Date,Description,Amount,Foreign Spend Amount\n"
                "2026-07-01,BISTRO,110,100 AUD\n",
                encoding="utf-8",
            )
            expense = Expense(
                source_file=receipt,
                expense_id="",
                date="2026-07-01",
                supplier_name="Bistro",
                expense_type="meal",
                amount=300,
                currency="AUD",
                line_items=[
                    LineItem(description="Dinner", amount=240),
                    LineItem(
                        description="Wine",
                        amount=60,
                        is_alcohol=True,
                        alcohol_confidence=0.99,
                    ),
                ],
            )
            save_line_item_review(trip, [expense])
            set_expense_review(trip, receipt.name, {"number_of_people": 3})

            with patch("nlp_expenses.generator.parse_receipt", return_value=expense):
                reconciliation = sync_reconciliation(trip, root, llm_mode="off")
            transaction = reconciliation["transactions"][0]
            set_manual_match(trip, transaction["group_id"], receipt.name, use_auto=False)

            preview = consolidation_view(root, trip)
            result = preview["expenses"][0]
            self.assertEqual(result["claimable_original"], 80.0)
            self.assertEqual(result["statement_purchase_amount_used"], 100.0)
            self.assertEqual(result["statement_purchase_currency"], "AUD")
            self.assertEqual(result["fx_rate"], 1.1)
            self.assertEqual(result["claimable_cad"], 88.0)

            with patch("nlp_expenses.generator.parse_receipt", return_value=expense):
                workbook_path = generate_review(trip, root, llm_mode="off")
            workbook = load_workbook(workbook_path, data_only=False)
            detail = workbook["expense_list"]
            self.assertIn("card_statements!$H:$H", detail["T2"].value)
            self.assertIn("$K2/$V2", detail["L2"].value)
            self.assertIn("$T2-$F2/$I2", detail["V2"].value)
            self.assertEqual(detail["W1"].value, "accounting_basis_status")
            self.assertIn('"statement_person_share"', detail["M2"].value)
            self.assertIn("$K2*", detail["M2"].value)

    def test_malformed_statement_purchase_amount_falls_back_to_receipt_fx_basis(self):
        receipt = {
            "source_file": "flight.pdf",
            "amount": 3_649.43,
            "currency": "AUD",
            "included": True,
            "number_of_people": 1,
        }
        reconciled = {
            "cad_amount_used": 3_688.57,
            "cad_source": "statement",
            "statement_purchase_amount_used": 649.43,
            "statement_purchase_currency": "AUD",
        }

        result = calculate_expense_result(receipt, reconciled)

        self.assertEqual(result["fx_basis_amount_used"], 3_649.43)
        self.assertEqual(result["fx_basis_status"], "receipt_fallback_mismatch")
        self.assertEqual(result["fx_rate"], 1.010725)
        self.assertEqual(result["claimable_cad"], 3_688.57)

    def test_full_receipt_match_uses_exact_statement_cad_settlement(self):
        receipt = {
            "source_file": "breakfast.pdf",
            "expense_type": "meal",
            "amount": 11.50,
            "currency": "AUD",
            "included": True,
            "number_of_people": 1,
            "line_total": 11.50,
            "included_total": 11.50,
            "excluded_total": 0.0,
        }
        reconciled = {
            "cad_amount_used": 11.93,
            "cad_source": "statement",
            "statement_purchase_amount_used": 11.72,
            "statement_purchase_currency": "AUD",
        }

        result = calculate_expense_result(receipt, reconciled)

        self.assertEqual(result["fx_basis_status"], "statement_receipt_total")
        self.assertEqual(result["claimable_cad"], 11.93)

    def test_non_meal_receipt_correction_is_not_overridden_by_stale_synthetic_line(self):
        receipt = {
            "source_file": "flight.pdf",
            "expense_type": "flight",
            "amount": 3_649.43,
            "currency": "AUD",
            "included": True,
            "number_of_people": 1,
            "line_total": 3_579.43,
            "included_total": 3_579.43,
            "excluded_total": 0.0,
        }
        reconciled = {
            "cad_amount_used": 3_688.57,
            "cad_source": "statement",
            "statement_purchase_amount_used": 649.43,
            "statement_purchase_currency": "AUD",
        }

        result = calculate_expense_result(receipt, reconciled)

        self.assertEqual(result["claimable_original"], 3_649.43)
        self.assertEqual(result["claimable_cad"], 3_688.57)

    def test_unrelated_statement_transaction_can_be_excluded_and_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_statement-disposition", mode="ivado")
            complete_metadata(trip)
            receipt = trip / "expenses_receipts" / "taxi.pdf"
            receipt.write_bytes(b"fixture")
            statement = trip / "card_statements" / "card.csv"
            statement.write_text(
                "Date,Description,Amount,Foreign Spend Amount\n"
                "2026-07-01,TAXI,75,50 AUD\n"
                "2026-07-01,SPOTIFY,20,\n",
                encoding="utf-8",
            )
            expense = Expense(
                source_file=receipt,
                expense_id="",
                date="2026-07-01",
                supplier_name="Taxi",
                expense_type="transport",
                amount=50,
                currency="AUD",
                line_items=[LineItem(description="Taxi", amount=50)],
            )
            save_line_item_review(trip, [expense])
            with patch("nlp_expenses.generator.parse_receipt", return_value=expense):
                reconciliation = sync_reconciliation(trip, root, llm_mode="off")
            unrelated = next(
                item for item in reconciliation["transactions"] if "SPOTIFY" in item["description"]
            )

            excluded = set_transaction_decision(
                trip,
                unrelated["group_id"],
                "ignore",
                "Personal subscription outside the trip",
            )
            excluded_transaction = next(
                item for item in excluded["transactions"] if item["group_id"] == unrelated["group_id"]
            )
            self.assertTrue(excluded_transaction["ignored"])
            self.assertEqual(excluded["summary"]["needs_review_count"], 0)
            self.assertTrue(consolidation_view(root, trip)["is_ready"])

            restored = set_transaction_decision(trip, unrelated["group_id"], "keep")
            restored_transaction = next(
                item for item in restored["transactions"] if item["group_id"] == unrelated["group_id"]
            )
            self.assertFalse(restored_transaction["ignored"])
            self.assertEqual(restored_transaction["normalization_status"], "ok")
            self.assertEqual(restored["summary"]["needs_review_count"], 1)

    def test_manual_cad_override_is_shared_without_a_statement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_manual-cad", mode="ivado")
            complete_metadata(trip)
            receipt = trip / "expenses_receipts" / "taxi.pdf"
            receipt.write_bytes(b"fixture")
            expense = Expense(
                source_file=receipt,
                expense_id="",
                date="2026-07-01",
                supplier_name="Taxi",
                expense_type="transport",
                amount=50,
                currency="AUD",
                line_items=[LineItem(description="Taxi", amount=50)],
            )
            save_line_item_review(trip, [expense])
            set_expense_review(
                trip,
                receipt.name,
                {
                    "manual_cad_override": 45,
                    "manual_cad_note": "Card portal confirmation",
                },
            )
            preview = consolidation_view(root, trip)
            self.assertTrue(preview["is_ready"])
            self.assertEqual(preview["expenses"][0]["cad_source"], "manual")
            self.assertEqual(preview["expenses"][0]["fx_rate"], 0.9)

    def test_legacy_line_review_defaults_to_included_one_person_and_existing_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_legacy", mode="ivado")
            receipt = trip / "expenses_receipts" / "taxi.pdf"
            receipt.write_bytes(b"fixture")
            expense = Expense(
                source_file=receipt,
                expense_id="",
                date="2026-07-01",
                supplier_name="Taxi",
                expense_type="transport",
                amount=35,
                currency="CAD",
                line_items=[LineItem(description="Taxi", amount=35)],
            )
            save_line_item_review(trip, [expense])
            path = trip / ".nlp-expenses-line-items.json"
            state = json.loads(path.read_text(encoding="utf-8"))
            legacy = state["receipts"][0]
            for field in (
                "amount",
                "included",
                "number_of_people",
                "manual_cad_override",
                "manual_cad_note",
                "extracted",
                "field_overrides",
                "extracted_line_items",
            ):
                legacy.pop(field, None)
            path.write_text(json.dumps(state), encoding="utf-8")

            migrated = line_item_review_view(trip)["receipts"][0]
            self.assertEqual(migrated["amount"], 35)
            self.assertTrue(migrated["included"])
            self.assertEqual(migrated["number_of_people"], 1)
            self.assertEqual(migrated["field_issues"], [])


if __name__ == "__main__":
    unittest.main()
