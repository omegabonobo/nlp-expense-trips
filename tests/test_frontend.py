from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from werkzeug.serving import make_server

from nlp_expenses.line_items import save_line_item_review
from nlp_expenses.models import Expense, LineItem
from nlp_expenses.reconciliation import serialize_expense
from nlp_expenses.reconciliation_state import (
    reconciliation_input_fingerprint,
    save_reconciliation_state,
    statement_input_fingerprint,
)
from nlp_expenses.trips import ensure_trip
from nlp_expenses.ui import create_app

ROOT = Path(__file__).parents[1]
STATIC = ROOT / "nlp_expenses" / "static"
TEMPLATES = ROOT / "nlp_expenses" / "templates"
MODULES = [
    "app.js",
    "app-shell.js",
    "app-jobs.js",
    "app-trip-setup.js",
    "app-source-files.js",
    "app-receipts.js",
    "app-reconciliation.js",
    "app-finalization.js",
    "app-outputs.js",
]
PARTIALS = [
    "partials_sidebar.html",
    "partials_trip_overview.html",
    "partials_source_files.html",
    "partials_receipt_review.html",
    "partials_reconciliation.html",
    "partials_finalization.html",
    "partials_outputs.html",
    "partials_empty_state.html",
]


def chrome_binary() -> str | None:
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    return next(
        (candidate for candidate in candidates if candidate and Path(candidate).is_file()), None
    )


class FrontendStructureTests(unittest.TestCase):
    def test_workflow_modules_are_valid_javascript(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        for module in MODULES:
            with self.subTest(module=module):
                subprocess.run(
                    [node, "--check", str(STATIC / module)],
                    check=True,
                    capture_output=True,
                    text=True,
                )

    def test_template_uses_named_components_and_ordered_modules(self):
        template = (TEMPLATES / "index.html").read_text(encoding="utf-8")
        for partial in PARTIALS:
            self.assertTrue((TEMPLATES / partial).is_file())
            self.assertIn(f'include "{partial}"', template)
        script_positions = [template.index(f"filename='{module}'") for module in MODULES]
        self.assertEqual(script_positions, sorted(script_positions))


@unittest.skipUnless(chrome_binary(), "Chrome or Chromium is not installed")
class FrontendBrowserTests(unittest.TestCase):
    def test_main_workflows_failures_and_job_completion(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            trip = ensure_trip(root, "202608_browser-reliability", mode="company")
            receipt = trip / "expenses_receipts" / "sample.pdf"
            receipt.write_bytes(b"%PDF-1.4 browser test")
            expense = Expense(
                source_file=receipt,
                expense_id="BROWSER-1",
                date="2026-08-10",
                supplier_name="Browser Cafe",
                expense_type="meal",
                amount=24.0,
                currency="CAD",
                line_items=[LineItem(description="Lunch", amount=24.0)],
            )
            save_line_item_review(trip, [expense])
            serialized_expense = serialize_expense(expense)
            save_reconciliation_state(
                trip,
                {
                    "version": 1,
                    "mode": "company",
                    "synced_at": "2026-08-10T12:00:00",
                    "sync_scope": "all",
                    "input_fingerprint": reconciliation_input_fingerprint(trip),
                    "statement_input_fingerprint": statement_input_fingerprint(trip),
                    "expenses": [serialized_expense],
                    "extracted_expenses": [serialized_expense],
                    "transactions": [],
                    "manual_matches": {},
                    "invoice_overrides": {},
                    "manual_cad_overrides": {},
                    "warnings": [],
                },
            )
            app = create_app(root, access_token="browser-test-token")

            @app.after_request
            def inject_browser_checks(response):
                if response.mimetype != "text/html" or response.status_code != 200:
                    return response
                script = r"""
<script>
(async () => {
  const checks = {};
  const check = (name, condition) => { checks[name] = Boolean(condition); };
  try {
    document.getElementById("new-trip-button").click();
    check("trip_setup", document.getElementById("new-trip-dialog").open);
    document.getElementById("new-trip-dialog").close();

    document.getElementById("open-trip-metadata-button").click();
    check("trip_metadata", document.getElementById("trip-metadata-dialog").open);
    document.getElementById("trip-metadata-dialog").close();
    check("source_files", document.querySelectorAll(".drop-zone").length === 2);
    check("receipt_review", Boolean(document.querySelector(".receipt-scan-button")));
    check("reconciliation", Boolean(document.getElementById("sync-reconciliation-button")));
    check("finalization", Boolean(document.getElementById("finalize-button")));
    check("generation", Boolean(document.getElementById("generate-button")));
    check("archive_restore", Boolean(document.querySelector(".archive-trip-button")));

    document.querySelector(".edit-expense").click();
    check("receipt_editing", document.getElementById("expense-dialog").open);
    check("receipt_selected", document.querySelector('#expense-review-form [name="source_file"]').value === "sample.pdf");
    document.getElementById("expense-dialog").close();
    check("exception_queue", Boolean(document.getElementById("receipt-exception-queue")));
    check("full_receipt_review", Boolean(document.querySelector(".line-item-table")));
    const fieldException = document.querySelector('.review-exception[data-field="date"]');
    fieldException.click();
    await new Promise(resolve => window.setTimeout(resolve, 50));
    check("exception_field_link", document.getElementById("expense-dialog").open && document.activeElement.name === "date");
    document.getElementById("expense-dialog").close();

    document.querySelector(".open-receipt-matcher").click();
    check("card_matching", document.getElementById("card-match-dialog").open);
    document.getElementById("card-match-dialog").close();

    const originalFetch = window.fetch;
    let staleRefreshCalled = false;
    window.NLPExpenses.testHooks.refreshPage = () => { staleRefreshCalled = true; };
    window.fetch = async url => String(url).includes("/file-state")
      ? new Response(JSON.stringify({
          busy: false, file_state: { signature: "externally-changed-browser-signature" }
        }), { status: 200, headers: { "Content-Type": "application/json" } })
      : originalFetch(url);
    await new Promise(resolve => window.setTimeout(resolve, 2900));
    check("stale_state", staleRefreshCalled);

    const traveller = document.querySelector('#trip-metadata-form input[name="traveller"]');
    traveller.value = "Unsaved browser value";
    traveller.dispatchEvent(new Event("input", { bubbles: true }));
    window.fetch = async () => new Response(
      JSON.stringify({ error: "Expected browser failure" }),
      { status: 400, headers: { "Content-Type": "application/json" } }
    );
    try {
      await window.NLPExpenses.api("/browser-test-failure", { method: "POST", body: "{}" });
    } catch (failure) {
      check("api_failure", failure.message === "Expected browser failure");
    }
    check("failed_input_retained", traveller.value === "Unsaved browser value");

    let refreshCalled = false;
    window.NLPExpenses.testHooks.refreshPage = () => { refreshCalled = true; };
    window.fetch = async () => new Response(JSON.stringify({
      job: {
        id: "browser-job", status: "succeeded", stage: "complete",
        current: 1, total: 1, message: "Done", warnings: []
      }
    }), { status: 200, headers: { "Content-Type": "application/json" } });
    window.NLPExpenses.jobs.start("line_items", {
      id: "browser-job", status: "running", stage: "scan",
      current: 0, total: 1, message: "Scanning", warnings: []
    });
    await new Promise(resolve => window.setTimeout(resolve, 100));
    check("job_completed", document.getElementById("line-item-job-status").textContent === "succeeded");
    check("unsaved_work_preserved", !refreshCalled && Boolean(document.getElementById("pending-refresh-notice")));
    window.fetch = originalFetch;
  } catch (failure) {
    checks.harness_error = failure.message;
  }
  document.documentElement.dataset.browserChecks = btoa(JSON.stringify(checks));
})();
</script>
"""
                body = response.get_data(as_text=True).replace("</body>", f"{script}</body>")
                response.set_data(body)
                return response

            server = make_server("127.0.0.1", 0, app, threaded=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with tempfile.TemporaryDirectory() as browser_profile:
                    command = [
                        chrome_binary(),
                        "--headless=new",
                        "--disable-gpu",
                        "--disable-background-networking",
                        "--no-first-run",
                        "--no-default-browser-check",
                        f"--user-data-dir={browser_profile}",
                        "--virtual-time-budget=4200",
                        "--dump-dom",
                        (
                            f"http://127.0.0.1:{server.server_port}/"
                            f"?token=browser-test-token&trip={trip.name}"
                        ),
                    ]
                    try:
                        result = subprocess.run(
                            command,
                            check=True,
                            capture_output=True,
                            text=True,
                            timeout=15,
                        )
                        browser_output = result.stdout
                        browser_error = result.stderr
                    except subprocess.TimeoutExpired as failure:
                        # Some Chrome builds keep the process alive for page timers after
                        # --dump-dom has emitted the complete document. subprocess.run has
                        # already stopped it; the captured DOM remains valid test output.
                        browser_output = failure.stdout or ""
                        browser_error = failure.stderr or ""
                        if isinstance(browser_output, bytes):
                            browser_output = browser_output.decode("utf-8", errors="replace")
                        if isinstance(browser_error, bytes):
                            browser_error = browser_error.decode("utf-8", errors="replace")
            finally:
                server.shutdown()
                thread.join(timeout=5)

        html = browser_output
        self.assertIn('data-frontend-ready="complete"', html)
        self.assertNotIn("data-frontend-error=", html)
        encoded = re.search(r'data-browser-checks="([^"]+)"', html)
        self.assertIsNotNone(encoded, browser_error[-1000:])
        checks = json.loads(base64.b64decode(encoded.group(1)).decode("utf-8"))
        self.assertNotIn("harness_error", checks)
        self.assertTrue(checks)
        self.assertTrue(all(checks.values()), checks)
