from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from nlp_expenses import __version__
from nlp_expenses.cli import main


class CliTests(unittest.TestCase):
    def test_create_trip_uses_explicit_data_root(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            root = Path(tmp) / "shared-expenses"

            main(["--root", str(root), "create-trip", "202608_ivado-lab", "--mode", "arvine"])

            trip = root / "trips" / "202608_ivado-lab"
            self.assertTrue((trip / "expenses_receipts").is_dir())
            self.assertTrue((trip / "card_statements").is_dir())

    def test_no_command_prints_help_in_noninteractive_process(self):
        output = io.StringIO()
        with (
            patch("nlp_expenses.cli.sys.stdin.isatty", return_value=False),
            redirect_stdout(output),
        ):
            main([])

        self.assertIn("create-trip", output.getvalue())
        self.assertIn("--root", output.getvalue())

    def test_version_flag_reports_package_version(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            main(["--version"])

        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), f"nlp-expenses {__version__}")


if __name__ == "__main__":
    unittest.main()
