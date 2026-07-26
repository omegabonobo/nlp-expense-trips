from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from nlp_expenses.line_items import (
    add_line_item,
    apply_line_item_review,
    ensure_line_item_review_ready,
    line_item_review_view,
    reset_receipt_review,
    save_line_item_review,
    set_expense_review,
    set_line_item_review,
)
from nlp_expenses.models import Expense, LineItem, NormalizedTransaction
from nlp_expenses.trip_metadata import save_trip_metadata
from nlp_expenses.trips import ensure_trip
from nlp_expenses.workbook import build_arvine_workbook, build_workbook


def meal_expense(path: Path) -> Expense:
    return Expense(
        source_file=path,
        expense_id="MEAL-1",
        date="2026-07-01",
        supplier_name="Bistro",
        expense_type="meal",
        amount=115.0,
        currency="CAD",
        country="Canada",
        province="QC",
        gst_hst=5.0,
        qst=10.0,
        line_items=[
            LineItem(description="Dinner", amount=80.0, confidence=0.9),
            LineItem(
                description="French 75",
                amount=20.0,
                is_alcohol=True,
                included=False,
                confidence=0.9,
                alcohol_confidence=0.97,
                alcohol_reason="recognized cocktail",
                alcohol_matched_term="french 75",
            ),
            LineItem(description="GST/HST", amount=5.0, included=True, confidence=1.0, synthetic=True),
            LineItem(description="QST", amount=10.0, included=True, confidence=1.0, synthetic=True),
        ],
    )


class LineItemReviewTests(unittest.TestCase):
    def test_default_payer_override_survives_rescan_and_reset_restores_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_payer", mode="arvine")
            save_trip_metadata(
                trip,
                {
                    "claim_program": "arvine_only",
                    "default_paid_by": "arvine_corporate_bmo",
                    "payer_confirmed": True,
                },
            )
            receipt = trip / "expenses_receipts" / "meal.pdf"
            receipt.write_bytes(b"meal")
            expense = meal_expense(receipt)
            save_line_item_review(trip, [expense])
            self.assertEqual(
                line_item_review_view(trip)["receipts"][0]["paid_by"],
                "arvine_corporate_bmo",
            )

            set_expense_review(trip, receipt.name, {"paid_by": "employee_personal"})
            save_line_item_review(trip, [meal_expense(receipt)])
            rescanned = line_item_review_view(trip)["receipts"][0]
            self.assertEqual(rescanned["paid_by"], "employee_personal")
            self.assertTrue(rescanned["paid_by_overridden"])

            reset = reset_receipt_review(trip, receipt.name)["receipts"][0]
            self.assertEqual(reset["paid_by"], "arvine_corporate_bmo")
            self.assertFalse(reset["paid_by_overridden"])

            save_trip_metadata(
                trip,
                {
                    "claim_program": "arvine_only",
                    "default_paid_by": "employee_personal",
                    "payer_confirmed": True,
                },
            )
            updated_default = line_item_review_view(trip)["receipts"][0]
            self.assertEqual(updated_default["paid_by"], "employee_personal")
            self.assertEqual(updated_default["auto_paid_by"], "employee_personal")

    def test_whole_receipt_ivado_exclusion_keeps_a_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_receipt_exclusion", mode="ivado")
            receipt = trip / "expenses_receipts" / "meal.pdf"
            receipt.write_bytes(b"meal")
            save_line_item_review(trip, [meal_expense(receipt)])

            excluded = set_expense_review(
                trip,
                receipt.name,
                {
                    "included_in_arvine": True,
                    "included_in_ivado": False,
                    "ivado_exclusion_reason": "non_business",
                },
            )["receipts"][0]
            self.assertFalse(excluded["included_in_ivado"])
            self.assertEqual(excluded["ivado_exclusion_reason"], "non_business")

            restored = set_expense_review(
                trip,
                receipt.name,
                {
                    "included_in_ivado": True,
                    "ivado_exclusion_reason": "non_business",
                },
            )["receipts"][0]
            self.assertTrue(restored["included_in_ivado"])
            self.assertIsNone(restored["ivado_exclusion_reason"])

    def test_summary_excludes_zero_value_manual_alcohol_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_summary", mode="ivado")
            receipt = trip / "expenses_receipts" / "meal.pdf"
            receipt.write_bytes(b"meal")
            expense = meal_expense(receipt)
            expense.line_items.append(
                LineItem(
                    description="Alcohol adjustment - manual",
                    amount=0.0,
                    is_alcohol=True,
                    included=False,
                    alcohol_confidence=1.0,
                )
            )
            save_line_item_review(trip, [expense])

            summary = line_item_review_view(trip)["summary"]
            self.assertEqual(summary["alcohol_count"], 1)
            self.assertEqual(summary["excluded_count"], 1)
            self.assertEqual(summary["line_count"], 5)

    def test_ivado_deactivates_alcohol_and_user_can_reactivate_without_reclassifying(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_ivado", mode="ivado")
            receipt = trip / "expenses_receipts" / "meal.pdf"
            receipt.write_bytes(b"meal")
            save_line_item_review(trip, [meal_expense(receipt)])

            view = line_item_review_view(trip)
            cocktail = next(
                item for item in view["receipts"][0]["line_items"] if item["description"] == "French 75"
            )
            self.assertTrue(cocktail["is_alcohol"])
            self.assertFalse(cocktail["included"])
            self.assertEqual(view["summary"]["excluded_count"], 1)

            updated = set_line_item_review(
                trip,
                receipt.name,
                cocktail["line_id"],
                {"included": True},
            )
            cocktail = next(
                item for item in updated["receipts"][0]["line_items"] if item["description"] == "French 75"
            )
            self.assertTrue(cocktail["is_alcohol"])
            self.assertTrue(cocktail["included"])
            self.assertTrue(cocktail["inclusion_overridden"])

            fresh = meal_expense(receipt)
            self.assertTrue(apply_line_item_review(trip, [fresh], require_fresh=True))
            applied = next(item for item in fresh.line_items if item.description == "French 75")
            self.assertTrue(applied.is_alcohol)
            self.assertTrue(applied.included)
            self.assertEqual(fresh.corrected_amount_in_currency, 115.0)

            reset = reset_receipt_review(trip, receipt.name)
            cocktail = next(
                item for item in reset["receipts"][0]["line_items"] if item["description"] == "French 75"
            )
            self.assertFalse(cocktail["included"])
            self.assertFalse(cocktail["inclusion_overridden"])

    def test_arvine_flags_alcohol_but_includes_it_until_user_excludes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_arvine", mode="arvine")
            receipt = trip / "expenses_receipts" / "meal.pdf"
            receipt.write_bytes(b"meal")
            save_line_item_review(trip, [meal_expense(receipt)])
            view = line_item_review_view(trip)
            cocktail = next(
                item for item in view["receipts"][0]["line_items"] if item["description"] == "French 75"
            )
            self.assertTrue(cocktail["included"])
            self.assertTrue(cocktail["is_alcohol"])

            updated = set_line_item_review(
                trip,
                receipt.name,
                cocktail["line_id"],
                {"included": False, "note": "Not reimbursable"},
            )
            receipt_view = updated["receipts"][0]
            self.assertAlmostEqual(receipt_view["claimable_ratio"], 95 / 115)
            self.assertEqual(receipt_view["included_total"], 95.0)
            self.assertFalse(receipt_view["blocking"])
            ensure_line_item_review_ready(trip)

    def test_user_line_choice_survives_small_openai_wording_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_arvine-rescan", mode="arvine")
            receipt = trip / "expenses_receipts" / "meal.pdf"
            receipt.write_bytes(b"meal")
            first = meal_expense(receipt)
            first.line_items = [
                LineItem(
                    description="Balter Mpe,Ping",
                    amount=17.90,
                    is_alcohol=True,
                    alcohol_confidence=0.95,
                ),
                LineItem(description="Chicken Peis", amount=34.50),
            ]
            save_line_item_review(trip, [first], llm_mode="required")
            reviewed = line_item_review_view(trip)
            alcohol = next(item for item in reviewed["receipts"][0]["line_items"] if item["is_alcohol"])
            set_line_item_review(trip, receipt.name, alcohol["line_id"], {"included": False})

            second = meal_expense(receipt)
            second.line_items = [
                LineItem(
                    description="Balter Mpe, Ping 1",
                    amount=17.90,
                    is_alcohol=True,
                    alcohol_confidence=0.95,
                ),
                LineItem(description="Chicken Peis", amount=34.50),
            ]
            save_line_item_review(trip, [second], llm_mode="required")
            rescanned = line_item_review_view(trip)
            alcohol = next(item for item in rescanned["receipts"][0]["line_items"] if item["is_alcohol"])
            self.assertFalse(alcohol["included"])
            self.assertTrue(alcohol["inclusion_overridden"])

    def test_manually_added_line_survives_rescan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_manual-line-rescan", mode="arvine")
            receipt = trip / "expenses_receipts" / "meal.pdf"
            receipt.write_bytes(b"meal")
            expense = meal_expense(receipt)
            save_line_item_review(trip, [expense], llm_mode="required")
            add_line_item(trip, receipt.name, "Tip confirmed manually", 5.0)

            save_line_item_review(trip, [meal_expense(receipt)], llm_mode="required")
            descriptions = {
                item["description"]
                for item in line_item_review_view(trip)["receipts"][0]["line_items"]
            }
            self.assertIn("Tip confirmed manually", descriptions)

    def test_receipt_source_change_makes_review_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_stale", mode="ivado")
            receipt = trip / "expenses_receipts" / "meal.pdf"
            receipt.write_bytes(b"first")
            save_line_item_review(trip, [meal_expense(receipt)])
            self.assertFalse(line_item_review_view(trip)["stale"])
            receipt.write_bytes(b"changed")
            self.assertTrue(line_item_review_view(trip)["stale"])
            with self.assertRaisesRegex(ValueError, "current receipt line items"):
                ensure_line_item_review_ready(trip)

    def test_unreconciled_receipt_blocks_generation_only_when_a_line_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_gap", mode="ivado")
            receipt = trip / "expenses_receipts" / "meal.pdf"
            receipt.write_bytes(b"meal")
            expense = meal_expense(receipt)
            expense.line_items[-1] = LineItem(
                description="Unreconciled meal item - review",
                amount=10.0,
                included=True,
                synthetic=True,
            )
            save_line_item_review(trip, [expense])
            view = line_item_review_view(trip)
            self.assertEqual(view["receipts"][0]["status"], "review")
            self.assertTrue(view["receipts"][0]["blocking"])
            with self.assertRaisesRegex(ValueError, "Resolve line-item totals"):
                ensure_line_item_review_ready(trip)

    def test_workbooks_use_inclusion_and_arvine_proportional_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ivado = ensure_trip(root, "202607_ivado-book", mode="ivado")
            ivado_receipt = ivado / "expenses_receipts" / "meal.pdf"
            ivado_receipt.write_bytes(b"meal")
            ivado_expense = meal_expense(ivado_receipt)
            ivado_expense.expense_id = "MEAL-1"
            ivado_output = build_workbook(ivado, [ivado_expense], [])
            ivado_book = load_workbook(ivado_output, data_only=False)
            line_headers = [cell.value for cell in ivado_book["expense_line_items"][1]]
            self.assertEqual(line_headers[7:9], ["is_alcohol", "included"])
            self.assertIn("expense_line_items!$I:$I,TRUE", ivado_book["expense_list"]["J2"].value)

            arvine = ensure_trip(root, "202607_arvine-book", mode="arvine")
            arvine_receipt = arvine / "expenses_receipts" / "meal.pdf"
            arvine_receipt.write_bytes(b"meal")
            expense = meal_expense(arvine_receipt)
            save_line_item_review(arvine, [expense])
            view = line_item_review_view(arvine)
            cocktail = next(
                item for item in view["receipts"][0]["line_items"] if item["description"] == "French 75"
            )
            set_line_item_review(arvine, arvine_receipt.name, cocktail["line_id"], {"included": False})
            apply_line_item_review(arvine, [expense], require_fresh=True)
            statement = arvine / "card_statements" / "card.csv"
            statement.write_text("", encoding="utf-8")
            transaction = NormalizedTransaction(
                source_file=statement,
                source_row=2,
                provider="amex",
                transaction_group_id="G1",
                funding_leg_id="G1:1",
                transaction_date="2026-07-01",
                description="BISTRO",
                match_eligible=True,
                purchase_amount=115.0,
                purchase_currency="CAD",
                settlement_amount=115.0,
                settlement_currency="CAD",
                cad_amount=115.0,
                cad_completeness="complete",
            )
            output = build_arvine_workbook(arvine, [expense], [transaction])
            book = load_workbook(output, data_only=False)
            detail = book["expense_detail"]
            self.assertIn("expense_line_items!$I:$I,TRUE", detail["AM2"].value)
            self.assertEqual(detail["AN2"].value, '=IF($AL2="","",$AL2-$AM2)')
            self.assertIn("$AM2/$AL2", detail["AO2"].value)
            self.assertIn("$AO2", detail["U2"].value)
            self.assertIn("$AO2", detail["W2"].value)
            self.assertEqual(book.sheetnames[-1], "expense_line_items")


if __name__ == "__main__":
    unittest.main()
