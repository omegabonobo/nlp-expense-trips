from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from nlp_expenses.lifecycle import (
    approve_trip,
    export_approved_package,
    record_generated_workbook,
    set_trip_archived,
    trip_lifecycle,
)
from nlp_expenses.models import Expense
from nlp_expenses.trip_metadata import save_trip_metadata
from nlp_expenses.trips import ensure_trip
from nlp_expenses.ui_services import trip_summaries
from nlp_expenses.workbook import build_workbook


class LifecycleTests(unittest.TestCase):
    def prepare_trip(self, root: Path) -> tuple[Path, Path]:
        trip = ensure_trip(root, "202607_audit", mode="ivado")
        save_trip_metadata(
            trip,
            {
                "claim_program": "ivado_sponsored",
                "traveller": "Florent",
                "company": "Example Inc.",
                "sponsor": "IVADO Labs",
                "start_date": "2026-07-01",
                "end_date": "2026-07-02",
                "business_purpose": "Client meeting",
                "approver": "Reviewer",
                "payment_method": "Employee reimbursement",
                "default_paid_by": "employee_personal",
                "payer_confirmed": True,
            },
        )
        receipt = trip / "expenses_receipts" / "receipt.pdf"
        receipt.write_bytes(b"receipt version one")
        expense = Expense(
            source_file=receipt,
            expense_id="EXP-1",
            date="2026-07-01",
            supplier_name="Cafe",
            expense_type="meal",
            amount=20,
            currency="CAD",
        )
        workbook = trip / "expense_review_202607_audit_ivado_20260725-120000.xlsx"
        build_workbook(trip, [expense], [], output_path=workbook)
        record_generated_workbook(root, trip, workbook)
        return trip, workbook

    def test_approval_hashes_package_and_source_change_reopens_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip, workbook = self.prepare_trip(root)
            workbook.write_bytes(workbook.read_bytes() + b"manual edit")
            self.assertTrue(trip_lifecycle(root, trip)["current_generation"]["manually_modified"])

            approval = approve_trip(root, trip, workbook.name, "Reviewer", "Reviewed and approved.")
            self.assertTrue(approval["current"])
            self.assertEqual(trip_lifecycle(root, trip)["status"], "approved")
            package = export_approved_package(root, trip)

            with zipfile.ZipFile(package) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                receipt_entry = next(
                    entry
                    for entry in manifest["review_inputs"]["artifacts"]
                    if entry["kind"] == "receipt"
                )
                archived_receipt = archive.read(receipt_entry["path"])
                self.assertEqual(hashlib.sha256(archived_receipt).hexdigest(), receipt_entry["sha256"])
                self.assertEqual(
                    hashlib.sha256(archive.read(manifest["workbook"]["path"])).hexdigest(),
                    manifest["workbook"]["sha256"],
                )

            (trip / "expenses_receipts" / "receipt.pdf").write_bytes(b"receipt version two")
            lifecycle = trip_lifecycle(root, trip)
            self.assertFalse(lifecycle["approval_current"])
            self.assertEqual(lifecycle["status"], "reconciling")
            self.assertIsNotNone(lifecycle["approval"])
            with self.assertRaisesRegex(ValueError, "changed after approval"):
                export_approved_package(root, trip)

    def test_archive_filters_default_list_and_restore_keeps_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip, _workbook = self.prepare_trip(root)
            original = trip.resolve()
            set_trip_archived(trip, True)
            self.assertEqual(trip_lifecycle(root, trip)["status"], "archived")
            self.assertEqual(trip_summaries(root), [])
            self.assertEqual(trip_summaries(root, include_archived=True)[0]["name"], trip.name)
            set_trip_archived(trip, False)
            self.assertEqual(trip.resolve(), original)
            self.assertEqual(trip_summaries(root)[0]["name"], trip.name)


if __name__ == "__main__":
    unittest.main()
