from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

from nlp_expenses.config import ask_openai_for_run
from nlp_expenses.extraction.alcohol import is_alcohol
from nlp_expenses.extraction.receipts import find_date, find_invoice_date, heuristic_parse_receipt
from nlp_expenses.extraction.statements import parse_csv_statement
from nlp_expenses.generator import assign_simple_expense_ids, generate_review
from nlp_expenses.matching import close_split_amount, match_score
from nlp_expenses.matching import enrich_expenses_from_statements
from nlp_expenses.models import Expense, StatementTransaction
from nlp_expenses.trips import ensure_trip, validate_trip_name
from nlp_expenses.workbook import build_workbook


class CoreTests(unittest.TestCase):
    def test_trip_name_validation_and_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(validate_trip_name("202606_melbourne"))
            self.assertFalse(validate_trip_name("melbourne_202606"))
            trip = ensure_trip(root, "202606_melbourne")
            self.assertTrue((trip / "expenses_receipts").is_dir())
            self.assertTrue((trip / "card_statements").is_dir())

    def test_openai_prompt_can_decline_or_store_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("builtins.input", return_value="n"):
                api_key, _model = ask_openai_for_run(root)
            self.assertIsNone(api_key)
            self.assertFalse((root / ".env").exists())

            with patch("builtins.input", return_value="y"), patch(
                "nlp_expenses.config.getpass", return_value="sk-test"
            ):
                api_key, model = ask_openai_for_run(root)
            self.assertEqual(api_key, "sk-test")
            self.assertEqual(model, "gpt-5.2")
            self.assertIn("OPENAI_API_KEY=sk-test", (root / ".env").read_text(encoding="utf-8"))

    def test_filename_date_and_simple_expense_ids(self):
        self.assertEqual(find_date("Scanned_20260530 - Receipt.pdf"), "2026-05-30")
        expenses = [
            Expense(source_file=Path("a.pdf"), expense_id="", date="2026-05-30"),
            Expense(source_file=Path("b.pdf"), expense_id="", date=None),
        ]
        assign_simple_expense_ids(expenses)
        self.assertEqual(expenses[0].expense_id, "20260530_#1")
        self.assertEqual(expenses[1].expense_id, "yyyymmdd_#2")

    def test_asking_for_api_key_forces_llm_over_high_confidence_heuristics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202606_test")
            receipt = trip / "expenses_receipts" / "Scanned_20260601-test.pdf"
            receipt.write_bytes(b"dummy")
            mocked_expense = Expense(source_file=receipt, expense_id="", date="2026-06-01", supplier_name="LLM Cafe")
            with patch("nlp_expenses.generator.ask_openai_for_run", return_value=("sk-test", "gpt-test")), patch(
                "nlp_expenses.generator.parse_receipt", return_value=mocked_expense
            ) as parse_receipt_mock:
                output = generate_review(trip, root, llm_mode="ask")
            self.assertTrue(output.exists())
            self.assertEqual(parse_receipt_mock.call_args.kwargs["use_llm"], True)
            self.assertEqual(parse_receipt_mock.call_args.kwargs["force_llm"], True)

    def test_alcohol_detection(self):
        self.assertTrue(is_alcohol("Glass of shiraz"))
        self.assertTrue(is_alcohol("Pint lager"))
        self.assertTrue(is_alcohol("Canta"))
        self.assertTrue(is_alcohol("Pisco Sour"))
        self.assertTrue(is_alcohol("GLS 2023 JC Own Lobethal Chardonnay"))
        self.assertTrue(is_alcohol("Stomping Ground IPA"))
        self.assertFalse(is_alcohol("Flat white coffee"))
        self.assertFalse(is_alcohol("Sparkling Water"))

    def test_receipt_heuristic_parses_total_and_corrected_amount(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "receipt.pdf"
            file_path.write_bytes(b"dummy")
            text = "\n".join(
                [
                    "Fancy Hanks",
                    "1 Jun 2026",
                    "Burger 20.00",
                    "Beer 9.00",
                    "Total $29.00",
                ]
            )
            expense = heuristic_parse_receipt(file_path, text)
            self.assertEqual(expense.date, "2026-06-01")
            self.assertEqual(expense.currency, "AUD")
            self.assertAlmostEqual(expense.amount, 29.0)
            self.assertAlmostEqual(expense.corrected_amount_in_currency, 20.0)
            self.assertTrue(any(item.description == "Alcohol adjustment - manual" and item.is_alcohol for item in expense.line_items))

    def test_invoice_date_is_preferred_for_flights(self):
        text = "\n".join(
            [
                "FlightTo Class Date Departure Arrival",
                "GA0718 D 29May 22:05 05:30",
                "Issuing Airline and date IATA: GARUDA INDONESIA 25May26 : 15394643",
                "Total Amount : IDR 20598982",
            ]
        )
        self.assertEqual(find_invoice_date(text), "2026-05-25")
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "Garuda - DPS to MEL.pdf"
            file_path.write_bytes(b"dummy")
            expense = heuristic_parse_receipt(file_path, text)
            self.assertEqual(expense.date, "2026-05-25")
            self.assertEqual(expense.expense_type, "flight")
            receipt_total_items = [item for item in expense.line_items if item.description == "Receipt total"]
            self.assertEqual(len(receipt_total_items), 1)
            self.assertEqual(receipt_total_items[0].amount, 20598982.0)
            self.assertTrue(any(item.description == "Alcohol adjustment - manual" and item.is_alcohol for item in expense.line_items))

    def test_juni_scanned_receipt_uses_filename_date_and_restaurant_supplier(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "Scanned_20260530 - Receipt - May 30 2026 - 9-27 PM.pdf"
            file_path.write_bytes(b"dummy")
            text = "\n".join(
                [
                    "TAX INVOICE",
                    "TASLE ACCOUNT 14 = Id/Check 36143",
                    "136 Exhibition Street",
                    "Served by Beth Juni « Juni Restaurant 01",
                    "Covers : 3",
                    "30/8/2026 at 9:17 pm",
                    "Food Sales $ 222.00",
                    "1 x SALMON SASHIMI $ 76.00",
                    "2 « STOMPING GROUND IPA $ 28.00",
                    "2 « ASAHT 400ML $ 30.00",
                    "Total $ 314.00",
                ]
            )
            expense = heuristic_parse_receipt(file_path, text)
            self.assertEqual(expense.date, "2026-05-30")
            self.assertEqual(expense.supplier_name, "Juni Restaurant")
            self.assertEqual(expense.expense_type, "meal-dinner")
            self.assertTrue(any(item.is_alcohol for item in expense.line_items))

    def test_known_restaurant_cafe_names_classify_as_meals(self):
        cases = [
            ("Nigel\nCappuccino $5.50\nBanana Bread $6.00\nTotal $11.50", "meal-breakfast"),
            ("Reine & La Rue\nThank you for dining at Reine & La Rue\nTotal Inc Tax: $521.00", "meal-dinner"),
            ("GABRIEL\nCAPPUCINO x 4\npork belly Benedict 1 $27.00\nSubtotal $43.50", "meal-breakfast"),
            ("Tax ywvOl\nThame you for dining wan US Bt Farmers\nDaughte’s\nTotal $474.10", "meal-dinner"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for idx, (text, expected_type) in enumerate(cases):
                file_path = Path(tmp) / f"receipt-{idx}.pdf"
                file_path.write_bytes(b"dummy")
                expense = heuristic_parse_receipt(file_path, text)
                self.assertEqual(expense.expense_type, expected_type)

    def test_restaurant_additive_charges_are_line_items_but_included_gst_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "Gmail - Receipt from Gillott espresso #rFUK.pdf"
            file_path.write_bytes(b"dummy")
            text = "\n".join(
                [
                    "Gillott espresso",
                    "Purchase Date May 31, 2026",
                    "Eggs Benedict $23.00",
                    "Cappuccino $5.70 A classic espresso coffee topped with steamed milk and a thick, velvety foam. Mug (have here) ($1.00)",
                    "Dine In Small ($0.50)",
                    "A classic espresso coffee topped with steamed milk and a thick, velvety foam. Mug (have here) ($1.00)",
                    "weekend surcharge (15%) $4.30",
                    "Total includes GST $3.00",
                    "Total $33.00",
                ]
            )
            expense = heuristic_parse_receipt(file_path, text)
            items = {item.description: item.amount for item in expense.line_items}
            self.assertEqual(items["Cappuccino"], 5.70)
            self.assertEqual(items["weekend surcharge"], 4.30)
            self.assertNotIn("Total includes GST", items)
            self.assertNotIn("Dine In Small", items)
            self.assertFalse(any("classic espresso" in description.lower() for description in items))

    def test_ocr_decimal_variants_and_payment_total_are_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "Scanned_20260602-1936.pdf"
            file_path.write_bytes(b"dummy")
            text = "\n".join(
                [
                    "Craft and Wheelbarrow",
                    "02/06/2026",
                    "BEVERAGE",
                    "Balter Mpe,Ping 1 17.90",
                    "BEVERAGE Total $17.90",
                    "FOOD",
                    "Chicken Peis 1 34,50",
                    "FOOD Total $34.50",
                    "AMERICAN EXPR. $53. 32",
                    "Natl. Handling Fee $0.92",
                    "GST Included $4.85",
                ]
            )
            expense = heuristic_parse_receipt(file_path, text)
            self.assertEqual(expense.amount, 53.32)
            items = {item.description: item.amount for item in expense.line_items}
            self.assertEqual(items["Chicken Peis"], 34.50)
            self.assertEqual(items["Natl. Handling Fee"], 0.92)
            self.assertFalse(any(amount == 4.85 for amount in items.values()))

    def test_tax_summary_rows_do_not_become_restaurant_line_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "Scanned_20260604-2259.pdf"
            file_path.write_bytes(b"dummy")
            text = "\n".join(
                [
                    "Reine & La Rue",
                    "2026-06-04 22:58:15",
                    "Canta $28.00",
                    "Pisco Sour $25.00",
                    "Bread x 3 $27.00",
                    "GLS 2023 JC Own Lobethal Chardonnay $25 .00",
                    "Subtotal: $521.00",
                    "Total ex tax: $473.63",
                    "- Tax Free $0.00",
                    "- GST $47.37",
                    "Total Inc Tax: $521 00",
                ]
            )
            expense = heuristic_parse_receipt(file_path, text)
            self.assertEqual(expense.amount, 521.0)
            items = {item.description: item.amount for item in expense.line_items}
            alcohol_items = {item.description for item in expense.line_items if item.is_alcohol}
            self.assertNotIn("Total ex tax", items)
            self.assertNotIn("GST", items)
            self.assertNotIn("Total Inc Tax", items)
            self.assertTrue(any("Chardonnay" in description for description in items))
            self.assertIn("Canta", alcohol_items)
            self.assertIn("Pisco Sour", alcohol_items)
            self.assertTrue(any("Chardonnay" in description for description in alcohol_items))

    def test_positive_meal_shortfall_gets_explicit_review_gap_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "Scanned_20260605-1429.pdf"
            file_path.write_bytes(b"dummy")
            text = "\n".join(
                [
                    "Gabriel",
                    "5 Jun 2026",
                    "pork belly Benedict 1 $27.00",
                    "DOUBLE ESPRESSO 1 $5.00",
                    "Cinnamon Scroll 1 $6.50",
                    "Subtotal $43.50",
                    "Card surcharge (1.5%) $0.85",
                ]
            )
            expense = heuristic_parse_receipt(file_path, text)
            gap_items = [item for item in expense.line_items if item.description == "Unreconciled meal item - review"]
            self.assertEqual(len(gap_items), 1)
            self.assertAlmostEqual(gap_items[0].amount, 5.0)
            self.assertIn("Unreconciled meal item line added", expense.review_note)

    def test_implausible_restaurant_amounts_are_not_accepted_as_menu_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "Scanned_20260605-1429.pdf"
            file_path.write_bytes(b"dummy")
            text = "\n".join(
                [
                    "GABRIEL",
                    "BIPINKUMAR $9 676 584 984",
                    "CAPPUCINO $5.50",
                    "pork belly Benedict $27.00",
                    "DOUBLE ESPRESSO ~« 1 $5.00",
                    "Cinnamon Scroll * 1 $6.50",
                    "Subtotal $38.50",
                    "Card surcharge (1.5%) $085",
                ]
            )
            expense = heuristic_parse_receipt(file_path, text)
            self.assertEqual(expense.amount, 39.35)
            self.assertNotIn(984.0, [item.amount for item in expense.line_items])
            self.assertNotIn(9.0, [item.amount for item in expense.line_items])
            self.assertIn(0.85, [item.amount for item in expense.line_items])

    def test_statement_merchant_can_rescue_poor_supplier_name(self):
        expense = Expense(
            source_file=Path("receipt.pdf"),
            expense_id="20260531_#6",
            date="2026-05-31",
            supplier_name="Tax ywvOl",
            expense_type="meal-dinner",
            amount=119.84,
            currency="AUD",
            raw_text="Farmers Daughte’s Total $119.84",
        )
        transaction = StatementTransaction(
            source_file=Path("statement.xls"),
            date="2026-05-31",
            description="ZLR*FARMERS DAUGHTER FA MELBOURNE",
            amount_cad=122.01,
            foreign_amount=119.84,
            foreign_currency="AUD",
            suggested_expense_id="20260531_#6",
            match_confidence=0.65,
        )
        enrich_expenses_from_statements([expense], [transaction])
        self.assertEqual(expense.supplier_name, "Farmers Daughters")

    def test_parse_csv_statement(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "statement.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["Date", "Description", "Amount", "Foreign Spend Amount"])
                writer.writeheader()
                writer.writerow(
                    {
                        "Date": "2026-06-01",
                        "Description": "FANCY HANKS MELBOURNE",
                        "Amount": "35.18",
                        "Foreign Spend Amount": "34.56 AUSTRALIAN DOLLAR",
                    }
                )
            rows = parse_csv_statement(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].date, "2026-06-01")
            self.assertEqual(rows[0].foreign_currency, "AUD")

    def test_match_score_uses_date_amount_and_supplier(self):
        expense = Expense(
            source_file=Path("receipt.pdf"),
            expense_id="EXP-1",
            date="2026-06-01",
            supplier_name="Fancy Hanks",
            amount=34.56,
            currency="AUD",
        )
        tx = StatementTransaction(
            source_file=Path("statement.csv"),
            date="2026-06-01",
            description="BBBQ - FANCY HANKS MELBOURNE",
            amount_cad=35.18,
            foreign_amount=34.56,
            foreign_currency="AUD",
        )
        self.assertGreaterEqual(match_score(expense, tx), 0.72)

    def test_split_amount_can_still_match_statement(self):
        self.assertTrue(close_split_amount(400.0, 100.0))
        expense = Expense(
            source_file=Path("receipt.pdf"),
            expense_id="EXP-1",
            date="2026-06-01",
            supplier_name="Fancy Hanks",
            amount=400.0,
            currency="AUD",
        )
        tx = StatementTransaction(
            source_file=Path("statement.csv"),
            date="2026-06-01",
            description="BBBQ - FANCY HANKS MELBOURNE",
            amount_cad=101.80,
            foreign_amount=100.0,
            foreign_currency="AUD",
        )
        self.assertGreaterEqual(match_score(expense, tx), 0.72)

    def test_workbook_formulas_and_empty_statement_tab(self):
        with tempfile.TemporaryDirectory() as tmp:
            trip = Path(tmp) / "202606_test"
            trip.mkdir()
            expense = Expense(
                source_file=Path("receipt.pdf"),
                expense_id="EXP-1",
                date="2026-06-01",
                supplier_name="Cafe",
                expense_type="meal",
                amount=10,
                currency="AUD",
                corrected_amount_in_currency=8,
                confidence=0.8,
            )
            transaction = StatementTransaction(source_file=Path("statement.csv"), date="2026-06-02", description="Unmatched", amount_cad=99)
            output = build_workbook(trip, [expense], [transaction])
            wb = load_workbook(output, data_only=False)
            self.assertEqual(wb["expense_list"]["E1"].value, "amount_in_currency")
            self.assertEqual(wb["expense_list"]["F1"].value, "receipt_total_in_currency")
            self.assertEqual(wb["expense_list"]["G1"].value, "amount_check")
            self.assertEqual(wb["expense_list"]["I1"].value, "number_of_person")
            self.assertEqual(wb["expense_list"]["I2"].value, 1)
            self.assertEqual(
                wb["expense_list"]["E2"].value,
                '=IF(COUNTIFS(expense_line_items!$A:$A,$A2,expense_line_items!$F:$F,">0")=0,"",SUMIFS(expense_line_items!$F:$F,expense_line_items!$A:$A,$A2))',
            )
            self.assertEqual(
                wb["expense_list"]["G2"].value,
                '=IF(OR($E2="",$F2=""),"missing",IF(ABS($E2-$F2)<=MAX(0.05,$F2*0.03),"ok","mismatch"))',
            )
            self.assertEqual(
                wb["expense_list"]["J2"].value,
                '=IF($E2="","",($E2-SUMIFS(expense_line_items!$F:$F,expense_line_items!$A:$A,$A2,expense_line_items!$H:$H,TRUE))/IF(OR($I2="",$I2=0),1,$I2))',
            )
            self.assertEqual(wb["expense_list"]["K2"].value, '=IFERROR(SUMIFS(card_statements!$C:$C,card_statements!$D:$D,$A2),"")')
            self.assertEqual(wb["expense_list"]["L2"].value, '=IF(OR($K2="",$E2="",$E2=0),"",$K2/($E2/IF(OR($I2="",$I2=0),1,$I2)))')
            self.assertEqual(wb["expense_list"]["M2"].value, '=IF(OR($J2="",$L2=""),"",$J2*$L2)')
            self.assertEqual(wb["card_statements"]["A1"].value, "date")
            self.assertEqual(wb["expense_list"]["I2"].fill.fgColor.rgb, "00FFF2CC")
            self.assertEqual(wb["card_statements"]["D2"].fill.fgColor.rgb, "00C00000")

    def test_integration_existing_melbourne_receipt_count(self):
        root = Path(__file__).resolve().parents[1]
        trip = root / "trips" / "202606_melbourne"
        if not trip.exists():
            self.skipTest("fixture trip not present")
        output = generate_review(trip, root, llm_mode="off")
        wb = load_workbook(output, read_only=True)
        self.assertEqual(wb["expense_list"].max_row - 1, 17)


if __name__ == "__main__":
    unittest.main()
