from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from openpyxl import load_workbook

from nlp_expenses.trip_manifest import (
    build_trip_manifest_records,
    validate_manifest_records,
    write_trip_manifest,
)
from nlp_expenses.trip_metadata import save_trip_metadata
from nlp_expenses.trips import ensure_trip
from nlp_expenses.workbook import build_ivado_claim_workbook

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "contracts" / "trip-reimbursement-manifest.v2.schema.json"
EXAMPLE_PATH = REPO_ROOT / "examples" / "ivado-trip-manifest-v2.ndjson"


class InterprojectContractTests(unittest.TestCase):
    def test_ivado_adapter_lists_a_whole_receipt_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            trip = Path(tmp)
            output = trip / "ivado.xlsx"
            records = [
                {
                    "kind": "receipt",
                    "id": "RCPT-1",
                    "document_date": "2026-07-21",
                    "vendor": "Taxi",
                    "description": "Personal detour",
                    "expense_type": "transport",
                    "source_file": "taxi.pdf",
                    "currency": "CAD",
                    "total": 75.0,
                    "total_cad": 75.0,
                    "ivado_claimable_cad": 0.0,
                    "ivado_excluded_cad": 75.0,
                    "paid_by": "employee_personal",
                    "included_in_arvine": True,
                    "included_in_ivado": False,
                    "ivado_exclusion_reason": "non_business",
                    "line_items": [],
                },
                {
                    "kind": "trip_report",
                    "claim_program": "ivado_sponsored",
                    "report_id": "TRIP-QA",
                    "trip_id": "qa",
                    "report_date": "2026-07-22",
                    "employee": {"legal_name": "QA Traveller"},
                    "company": {"legal_name": "Arvine Labs Inc."},
                    "sponsor": {"legal_name": "IVADO Labs"},
                    "description": "QA trip",
                    "ivado_claim_total_cad": 0.0,
                    "ivado_excluded_total_cad": 75.0,
                },
            ]

            build_ivado_claim_workbook(trip, records, output)
            workbook = load_workbook(output, data_only=False)
            exclusion = workbook["Exclusions"]
            self.assertEqual(exclusion["D2"].value, "Personal detour")
            self.assertEqual(exclusion["E2"].value, 75.0)
            self.assertEqual(exclusion["F2"].value, "non_business")

    def test_contract_declares_independent_program_and_payer_enums(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

        self.assertEqual(
            schema["$defs"]["claimProgram"]["enum"],
            ["arvine_only", "ivado_sponsored"],
        )
        self.assertEqual(
            schema["$defs"]["paidBy"]["enum"],
            ["employee_personal", "arvine_corporate_bmo"],
        )

    def test_example_reconciles_full_employee_and_net_ivado_amounts(self):
        records = [
            json.loads(line)
            for line in EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        receipts = [record for record in records if record["kind"] == "receipt"]
        report = next(record for record in records if record["kind"] == "trip_report")

        self.assertAlmostEqual(
            sum(record["arvine_reimbursable_cad"] for record in receipts),
            report["employee_reimbursement_total_cad"],
            places=2,
        )
        self.assertAlmostEqual(
            sum(record["ivado_claimable_cad"] for record in receipts),
            report["ivado_claim_total_cad"],
            places=2,
        )
        self.assertAlmostEqual(
            sum(record["ivado_excluded_cad"] for record in receipts),
            report["ivado_excluded_total_cad"],
            places=2,
        )
        self.assertAlmostEqual(
            report["employee_reimbursement_total_cad"],
            report["ivado_claim_total_cad"] + report["ivado_excluded_total_cad"],
            places=2,
        )

    def test_consumer_contract_copy_matches_when_repo_is_present(self):
        consumer_schema = (
            REPO_ROOT.parent.parent
            / "arvine-accounting-expenses"
            / "contracts"
            / SCHEMA_PATH.name
        )
        if not consumer_schema.exists():
            self.skipTest("arvine-accounting-expenses checkout is not present")

        self.assertEqual(
            SCHEMA_PATH.read_bytes(),
            consumer_schema.read_bytes(),
            "Update both contract copies together and bump the version when semantics change.",
        )

    def test_producer_builds_and_writes_valid_ivado_contract_from_reviewed_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_montreal", mode="ivado")
            metadata = save_trip_metadata(
                trip,
                {
                    "claim_program": "ivado_sponsored",
                    "traveller": "Florent Voumard",
                    "company": "Arvine Labs Inc.",
                    "sponsor": "IVADO Labs",
                    "start_date": "2026-07-20",
                    "end_date": "2026-07-22",
                    "business_purpose": "Montreal business trip",
                    "approver": "Reviewer",
                    "payment_method": "Employee reimbursement",
                    "default_paid_by": "employee_personal",
                    "payer_confirmed": True,
                },
            )
            view = {
                "is_ready": True,
                "claim_program": "ivado_sponsored",
                "metadata": metadata,
                "expenses": [
                    {
                        "receipt_id": "ignored-order-id",
                        "source_file": "restaurant.pdf",
                        "date": "2026-07-21",
                        "vendor": "Restaurant",
                        "description": "Business dinner",
                        "expense_type": "meal",
                        "amount": 115.0,
                        "subtotal": 115.0,
                        "currency": "CAD",
                        "paid_by": "employee_personal",
                        "included_in_arvine": True,
                        "included_in_ivado": True,
                        "number_of_people": 1,
                        "total_cad": 115.0,
                        "arvine_reimbursable_cad": 115.0,
                        "corporate_paid_cad": 0.0,
                        "ivado_claimable_cad": 95.0,
                        "ivado_excluded_cad": 20.0,
                        "cad_amount_used": 115.0,
                        "fx_rate": 1.0,
                        "fx_basis_status": "statement_receipt_total",
                        "gst_hst": 0.0,
                        "qst": 0.0,
                        "line_items": [
                            {
                                "line_id": "food",
                                "description": "Food and tip",
                                "amount": 95.0,
                                "is_alcohol": False,
                                "included_in_arvine": True,
                                "included_in_ivado": True,
                            },
                            {
                                "line_id": "wine",
                                "description": "Wine",
                                "amount": 20.0,
                                "is_alcohol": True,
                                "included_in_arvine": True,
                                "included_in_ivado": False,
                                "ivado_exclusion_reason": "alcohol",
                            },
                        ],
                    }
                ],
                "summary": {
                    "employee_reimbursement_total_cad": 115.0,
                    "corporate_paid_total_cad": 0.0,
                    "ivado_claim_total_cad": 95.0,
                    "ivado_excluded_total_cad": 20.0,
                    "blocking_count": 0,
                },
                "accounting": {
                    "rows": [
                        {"account": "Meals – Deductible (50%)", "amount_cad": 57.5},
                        {"account": "Meals – Non-deductible (50%)", "amount_cad": 57.5},
                    ]
                },
            }
            records = build_trip_manifest_records(root, trip, view=view)
            validate_manifest_records(records)
            self.assertEqual([record["kind"] for record in records], ["receipt", "trip_report"])
            self.assertEqual(records[0]["arvine_reimbursable_cad"], 115.0)
            self.assertEqual(records[0]["ivado_claimable_cad"], 95.0)
            self.assertEqual(len(records[1]["settlement_legs"]), 2)

            output = write_trip_manifest(root, trip, view=view)
            written = [
                json.loads(line)
                for line in output.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(written, records)

            consumer_parser = (
                REPO_ROOT.parent.parent
                / "arvine-accounting-expenses"
                / "src"
                / "lib"
                / "manifest.mjs"
            )
            if consumer_parser.is_file():
                script = (
                    "import { parseManifestFile } from "
                    f"{json.dumps(consumer_parser.as_uri())};"
                    "const rows = await parseManifestFile(process.argv[1]);"
                    "if (rows.length !== 2) process.exit(2);"
                )
                subprocess.run(
                    ["node", "--input-type=module", "-e", script, str(output)],
                    check=True,
                    capture_output=True,
                    text=True,
                )


if __name__ == "__main__":
    unittest.main()
