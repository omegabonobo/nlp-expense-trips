from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from nlp_expenses.trips import ensure_trip, trip_mode
from nlp_expenses.extraction.text import HEIC_AVAILABLE
from nlp_expenses.ui_services import (
    change_trip_mode,
    create_trip,
    create_trip_name,
    open_workbook,
    remove_source_file,
    resolve_trip,
    reveal_in_finder,
    store_upload,
    trip_details,
    trip_file_state,
    versioned_output_path,
)


class UIServiceTests(unittest.TestCase):
    def test_trip_name_creation_and_default_arvine_mode(self):
        self.assertEqual(create_trip_name("2026-07", "Montréal Client Meetings"), "202607_montreal-client-meetings")
        with self.assertRaises(ValueError):
            create_trip_name("2026-13", "Montreal")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = create_trip(root, "2026-07", "Montréal Client Meetings")
            self.assertEqual(trip.name, "202607_montreal-client-meetings")
            self.assertEqual(trip_mode(trip), "arvine")
            change_trip_mode(root, trip.name, "ivado")
            self.assertEqual(trip_mode(trip), "ivado")

    def test_upload_duplicate_collision_and_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_montreal", mode="arvine")
            first = store_upload(root, trip.name, "receipts", "café receipt.pdf", BytesIO(b"%PDF-first"))
            self.assertEqual(first.status, "uploaded")
            self.assertEqual(first.name, "cafe_receipt.pdf")

            duplicate = store_upload(root, trip.name, "receipts", "copy.pdf", BytesIO(b"%PDF-first"))
            self.assertEqual(duplicate.status, "duplicate")
            self.assertEqual(duplicate.name, first.name)

            collision = store_upload(root, trip.name, "receipts", "café receipt.pdf", BytesIO(b"%PDF-second"))
            self.assertEqual(collision.name, "cafe_receipt-2.pdf")
            remove_source_file(root, trip.name, "receipts", collision.name)
            self.assertFalse((trip / "expenses_receipts" / collision.name).exists())

    def test_receipt_upload_validates_images_and_supports_heic_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_images", mode="arvine")
            with self.assertRaisesRegex(ValueError, "could not be decoded"):
                store_upload(root, trip.name, "receipts", "renamed.png", BytesIO(b"not an image"))
            self.assertFalse((trip / "expenses_receipts" / "renamed.png").exists())

            from PIL import Image

            png = BytesIO()
            Image.new("RGB", (12, 12), "blue").save(png, format="PNG")
            png.seek(0)
            uploaded = store_upload(root, trip.name, "receipts", "scan.png", png)
            self.assertEqual(uploaded.status, "uploaded")

            if HEIC_AVAILABLE:
                import pillow_heif

                heic = BytesIO()
                pillow_heif.from_pillow(Image.new("RGB", (12, 12), "green")).save(heic)
                heic.seek(0)
                uploaded_heic = store_upload(root, trip.name, "receipts", "iphone.heic", heic)
                self.assertEqual(uploaded_heic.status, "uploaded")

    def test_statement_formats_follow_trip_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_montreal", mode="arvine")
            with self.assertRaisesRegex(ValueError, "not supported"):
                store_upload(root, trip.name, "statements", "statement.pdf", BytesIO(b"pdf"))
            change_trip_mode(root, trip.name, "ivado")
            result = store_upload(root, trip.name, "statements", "statement.pdf", BytesIO(b"pdf"))
            self.assertEqual(result.status, "uploaded")

    def test_trip_and_file_paths_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_montreal", mode="arvine")
            with self.assertRaises(ValueError):
                resolve_trip(root, "../outside")
            with self.assertRaises(ValueError):
                remove_source_file(root, trip.name, "receipts", "../outside.pdf")

    def test_versioned_outputs_never_reuse_a_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            trip = Path(tmp) / "202607_montreal"
            trip.mkdir()
            moment = datetime(2026, 7, 14, 15, 30, 12)
            first = versioned_output_path(trip, "arvine", now=moment)
            first.write_bytes(b"manual edits")
            second = versioned_output_path(trip, "arvine", now=moment)
            self.assertNotEqual(first, second)
            self.assertTrue(second.name.endswith("-2.xlsx"))
            self.assertEqual(first.read_bytes(), b"manual edits")

    def test_trip_file_state_detects_finder_side_changes_and_ignores_hidden_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_montreal", mode="arvine")
            initial = trip_file_state(root, trip.name)

            hidden = trip / "expenses_receipts" / ".upload-in-progress"
            hidden.write_bytes(b"partial")
            self.assertEqual(trip_file_state(root, trip.name), initial)

            receipt = trip / "expenses_receipts" / "receipt.pdf"
            receipt.write_bytes(b"first")
            added = trip_file_state(root, trip.name)
            self.assertNotEqual(added["signature"], initial["signature"])
            self.assertEqual(added["receipts"], 1)

            receipt.write_bytes(b"changed contents")
            changed = trip_file_state(root, trip.name)
            self.assertNotEqual(changed["signature"], added["signature"])

            (trip / "card_statements" / "card.csv").write_text(
                "Date,Description,Amount\n2026-07-01,Cafe,12.50\n",
                encoding="utf-8",
            )
            statement_added = trip_file_state(root, trip.name)
            self.assertEqual(statement_added["statements"], 1)
            self.assertNotEqual(statement_added["signature"], changed["signature"])

    def test_nested_receipts_are_listed_counted_and_removable_by_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_montreal", mode="arvine")
            nested = trip / "expenses_receipts" / "meta ads" / "2026-06"
            nested.mkdir(parents=True)
            receipt = nested / "invoice.pdf"
            receipt.write_bytes(b"nested")
            hidden_folder = trip / "expenses_receipts" / ".organizer"
            hidden_folder.mkdir()
            (hidden_folder / "ignored.pdf").write_bytes(b"hidden")

            state = trip_file_state(root, trip.name)
            details = trip_details(root, trip.name)
            self.assertEqual(state["receipts"], 1)
            self.assertEqual(
                [item["name"] for item in details["receipts"]],
                ["meta ads/2026-06/invoice.pdf"],
            )

            remove_source_file(
                root,
                trip.name,
                "receipts",
                "meta ads/2026-06/invoice.pdf",
            )
            self.assertFalse(receipt.exists())

    def test_macos_actions_only_receive_safe_trip_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trip = ensure_trip(root, "202607_montreal", mode="arvine")
            workbook = trip / "expense_review_202607_montreal_arvine_20260714-153012.xlsx"
            workbook.write_bytes(b"xlsx")
            with patch("nlp_expenses.ui_services.subprocess.run") as run:
                open_workbook(root, trip.name, workbook.name)
                reveal_in_finder(root, trip.name, "workbook", workbook.name)
            self.assertEqual(run.call_args_list[0].args[0], ["open", str(workbook.resolve())])
            self.assertEqual(run.call_args_list[1].args[0], ["open", "-R", str(workbook.resolve())])
            with self.assertRaises((ValueError, FileNotFoundError)):
                open_workbook(root, trip.name, "../outside.xlsx")


if __name__ == "__main__":
    unittest.main()
