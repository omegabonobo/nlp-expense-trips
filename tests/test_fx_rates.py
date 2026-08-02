from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nlp_expenses.fx_rates import FX_CACHE_FILE, WeeklyCadFxResolver
from nlp_expenses.models import Expense
from nlp_expenses.reconciliation import sync_reconciliation
from nlp_expenses.statement_normalizer import normalize_statement_files
from nlp_expenses.trips import ensure_trip


def write_csv(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


class WeeklyCadFxTests(unittest.TestCase):
    def test_qar_uses_official_peg_week_and_persists_auditable_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            trip = Path(tmp)
            requested: list[str] = []

            def fetch(url: str) -> dict:
                requested.append(url)
                return {
                    "observations": [
                        {"d": "2026-06-22", "FXUSDCAD": {"v": "1.4162"}},
                        {"d": "2026-06-23", "FXUSDCAD": {"v": "1.4200"}},
                        {"d": "2026-06-24", "FXUSDCAD": {"v": "1.4234"}},
                        {"d": "2026-06-25", "FXUSDCAD": {"v": "1.4204"}},
                        {"d": "2026-06-26", "FXUSDCAD": {"v": "1.4186"}},
                    ]
                }

            rate = WeeklyCadFxResolver(trip, fetch_json=fetch).resolve("QAR", "2026-06-24")
            self.assertEqual(rate.week_start, "2026-06-22")
            self.assertEqual(rate.week_end, "2026-06-28")
            self.assertEqual(rate.route, "QAR→USD→CAD")
            self.assertAlmostEqual(rate.cad_per_unit, 1.41972 / 3.64, places=10)
            self.assertEqual(len(requested), 1)
            self.assertIn("FXUSDCAD", requested[0])

            cache_path = trip / FX_CACHE_FILE
            self.assertTrue(cache_path.is_file())
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertIn("QAR:2026-06-22", cache["rates"])
            cached = WeeklyCadFxResolver(
                trip,
                fetch_json=lambda _url: self.fail("cached rate should avoid a network request"),
            ).resolve("QAR", "2026-06-26")
            self.assertEqual(cached, rate)

    def test_shared_fx_enrichment_applies_to_a_generic_future_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            trip = Path(tmp)
            statement = trip / "new-card.csv"
            write_csv(
                statement,
                ["Date", "Description", "Amount", "Currency"],
                [["2026-07-07", "NEW CARD HOTEL", 100, "USD"]],
            )

            def fetch(_url: str) -> dict:
                return {
                    "observations": [
                        {"d": "2026-07-06", "FXUSDCAD": {"v": "1.35"}},
                        {"d": "2026-07-07", "FXUSDCAD": {"v": "1.37"}},
                    ]
                }

            result = normalize_statement_files(
                [statement],
                fx_resolver=WeeklyCadFxResolver(trip, fetch_json=fetch),
            )
            self.assertEqual(result.errors, [])
            transaction = result.transactions[0]
            self.assertEqual(transaction.provider, "generic")
            self.assertEqual(transaction.cad_amount, 136.0)
            self.assertEqual(transaction.cad_completeness, "complete")
            self.assertEqual(transaction.cad_conversion_method, "weekly_average_direct")
            self.assertEqual(transaction.cad_conversion_route, "USD→CAD")
            self.assertEqual(transaction.cad_conversion_week_start, "2026-07-06")

    def test_standard_csv_uses_shared_fx_when_exact_cad_is_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            trip = Path(tmp)
            statement = trip / "standard-statement.csv"
            write_csv(
                statement,
                [
                    "transaction_date",
                    "description",
                    "purchase_amount",
                    "purchase_currency",
                ],
                [["2026-07-07", "STANDARD HOTEL", 100, "USD"]],
            )

            def fetch(_url: str) -> dict:
                return {
                    "observations": [
                        {"d": "2026-07-06", "FXUSDCAD": {"v": "1.35"}},
                        {"d": "2026-07-07", "FXUSDCAD": {"v": "1.37"}},
                    ]
                }

            result = normalize_statement_files(
                [statement],
                fx_resolver=WeeklyCadFxResolver(trip, fetch_json=fetch),
            )
            transaction = result.transactions[0]
            self.assertEqual(transaction.provider, "standard")
            self.assertEqual(transaction.cad_amount, 136.0)
            self.assertEqual(transaction.cad_completeness, "complete")
            self.assertEqual(
                transaction.cad_conversion_method,
                "weekly_average_direct",
            )

    def test_reconciliation_uses_shared_fx_layer_for_standard_statement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_new_card", mode="arvine")
            receipt_path = trip / "expenses_receipts" / "hotel.pdf"
            receipt_path.write_bytes(b"hotel")
            write_csv(
                trip / "card_statements" / "new-card.csv",
                [
                    "transaction_date",
                    "description",
                    "purchase_amount",
                    "purchase_currency",
                ],
                [["2026-07-07", "NEW CARD HOTEL", 100, "USD"]],
            )
            expense = Expense(
                source_file=receipt_path,
                expense_id="",
                date="2026-07-07",
                supplier_name="New Card Hotel",
                expense_type="hotel",
                amount=100,
                currency="USD",
            )
            payload = {
                "observations": [
                    {"d": "2026-07-06", "FXUSDCAD": {"v": "1.35"}},
                    {"d": "2026-07-07", "FXUSDCAD": {"v": "1.37"}},
                ]
            }
            with (
                patch("nlp_expenses.fx_rates.fetch_json_url", return_value=payload),
                patch(
                    "nlp_expenses.generator.parse_arvine_receipt",
                    return_value=expense,
                ),
            ):
                view = sync_reconciliation(trip, root, llm_mode="off")
            transaction = view["transactions"][0]
            self.assertEqual(transaction["provider"], "standard")
            self.assertEqual(transaction["cad_amount"], 136.0)
            self.assertEqual(transaction["cad_completeness"], "complete")
            self.assertEqual(
                transaction["cad_conversion_method"],
                "weekly_average_direct",
            )
            self.assertTrue((trip / FX_CACHE_FILE).is_file())

    def test_wise_uses_target_currency_ignores_exported_rate_and_negates_refunded_out_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            trip = Path(tmp)
            statement = trip / "wise.csv"
            headers = [
                "ID",
                "Status",
                "Direction",
                "Created on",
                "Finished on",
                "Source amount after fees",
                "Source fee amount",
                "Source currency",
                "Target amount after fees",
                "Target currency",
                "Exchange rate",
                "Source name",
                "Target name",
                "Category",
            ]
            write_csv(
                statement,
                headers,
                [
                    [
                        "CARD_TRANSACTION-1",
                        "COMPLETED",
                        "OUT",
                        "2026-06-24",
                        "2026-06-24",
                        900000,
                        20000,
                        "IDR",
                        100,
                        "QAR",
                        999,
                        "Wise",
                        "Hotel",
                        "card",
                    ],
                    [
                        "CARD_TRANSACTION-2",
                        "REFUNDED",
                        "OUT",
                        "2026-06-25",
                        "2026-06-25",
                        50000,
                        0,
                        "IDR",
                        5.95,
                        "QAR",
                        999,
                        "Wise",
                        "Hotel refund",
                        "card",
                    ],
                    [
                        "CARD_TRANSACTION-3",
                        "COMPLETED",
                        "OUT",
                        "2026-06-25",
                        "2026-06-25",
                        75,
                        2,
                        "CAD",
                        200,
                        "QAR",
                        999,
                        "Wise",
                        "CAD-funded meal",
                        "card",
                    ],
                    [
                        "CARD_TRANSACTION-4",
                        "REFUNDED",
                        "OUT",
                        "2026-06-25",
                        "2026-06-25",
                        0,
                        0,
                        "CAD",
                        0,
                        "CAD",
                        999,
                        "Wise",
                        "Zero refund audit",
                        "card",
                    ],
                ],
            )

            def fetch(_url: str) -> dict:
                return {
                    "observations": [
                        {"d": "2026-06-22", "FXUSDCAD": {"v": "1.42"}},
                    ]
                }

            result = normalize_statement_files(
                [statement],
                fx_resolver=WeeklyCadFxResolver(trip, fetch_json=fetch),
            )
            transactions = {
                transaction.transaction_group_id: transaction
                for transaction in result.transactions
            }
            purchase = transactions["CARD_TRANSACTION-1"]
            refund = transactions["CARD_TRANSACTION-2"]
            cad_funded = transactions["CARD_TRANSACTION-3"]
            zero_refund = transactions["CARD_TRANSACTION-4"]
            qar_rate = 1.42 / 3.64
            self.assertEqual(purchase.purchase_amount, 100)
            self.assertEqual(purchase.purchase_currency, "QAR")
            self.assertEqual(purchase.cad_amount, round(100 * qar_rate, 2))
            self.assertEqual(refund.transaction_type, "refund")
            self.assertEqual(refund.purchase_amount, -5.95)
            self.assertEqual(refund.cad_amount, round(-5.95 * qar_rate, 2))
            self.assertIn("REFUNDED", refund.review_note)
            self.assertEqual(cad_funded.cad_amount, 77)
            self.assertEqual(
                cad_funded.cad_conversion_method,
                "statement_exact_cad_settlement",
            )
            self.assertFalse(zero_refund.match_eligible)
            self.assertEqual(zero_refund.cad_completeness, "not_applicable")


if __name__ == "__main__":
    unittest.main()
