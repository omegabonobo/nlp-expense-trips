from __future__ import annotations

import json
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "contracts" / "trip-reimbursement-manifest.v2.schema.json"
EXAMPLE_PATH = REPO_ROOT / "examples" / "ivado-trip-manifest-v2.ndjson"


class InterprojectContractTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
