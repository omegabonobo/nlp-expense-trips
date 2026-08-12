from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nlp_expenses.config import load_dotenv, save_openai_settings
from nlp_expenses.storage import write_json_atomic, write_text_atomic


class StorageTests(unittest.TestCase):
    def test_atomic_json_write_is_private_and_preserves_unicode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "state.json"

            write_json_atomic(path, {"traveller": "Montréal", "amount": 12.5})

            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"amount": 12.5, "traveller": "Montréal"},
            )
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_failed_atomic_replace_keeps_original_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("original", encoding="utf-8")

            with (
                patch("nlp_expenses.storage.os.replace", side_effect=OSError("disk error")),
                self.assertRaisesRegex(OSError, "disk error"),
            ):
                write_text_atomic(path, "replacement")

            self.assertEqual(path.read_text(encoding="utf-8"), "original")
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_dotenv_skips_invalid_keys_and_rejects_multiline_settings(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            (root / ".env").write_text(
                "=missing-key\nINVALID-KEY=ignored\nVALID_KEY=kept\n",
                encoding="utf-8",
            )

            self.assertEqual(load_dotenv(root), {"VALID_KEY": "kept"})
            self.assertEqual(os.environ["VALID_KEY"], "kept")
            with self.assertRaisesRegex(ValueError, "must fit on one line"):
                save_openai_settings(root, "sk-test\nINJECTED=value", "gpt-test")


if __name__ == "__main__":
    unittest.main()
