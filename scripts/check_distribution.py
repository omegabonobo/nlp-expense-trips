#!/usr/bin/env python3
"""Verify that built archives contain every runtime and contributor asset."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path

from nlp_expenses import __version__

WHEEL_ASSETS = {
    "nlp_expenses/contracts/trip-reimbursement-manifest.v2.schema.json",
    "nlp_expenses/contracts/trip-reimbursement-manifest.v3.schema.json",
    "nlp_expenses/static/app.css",
    "nlp_expenses/static/app.js",
    "nlp_expenses/static/app-finalization.js",
    "nlp_expenses/static/app-jobs.js",
    "nlp_expenses/static/app-outputs.js",
    "nlp_expenses/static/app-receipts.js",
    "nlp_expenses/static/app-reconciliation.js",
    "nlp_expenses/static/app-shell.js",
    "nlp_expenses/static/app-source-files.js",
    "nlp_expenses/static/app-trip-setup.js",
    "nlp_expenses/static/standard-statement-template.csv",
    "nlp_expenses/templates/index.html",
    "nlp_expenses/templates/partials_empty_state.html",
    "nlp_expenses/templates/partials_finalization.html",
    "nlp_expenses/templates/partials_outputs.html",
    "nlp_expenses/templates/partials_receipt_review.html",
    "nlp_expenses/templates/partials_reconciliation.html",
    "nlp_expenses/templates/partials_sidebar.html",
    "nlp_expenses/templates/partials_source_files.html",
    "nlp_expenses/templates/partials_trip_overview.html",
}
SDIST_ASSETS = WHEEL_ASSETS | {
    ".env.example",
    "constraints-runtime.txt",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "Makefile",
    "docs/DEVELOPMENT.md",
    "docs/GETTING_STARTED.md",
    "examples/ivado-trip-manifest-v3.ndjson",
    "NLP Expenses.command",
    "scripts/check_distribution.py",
    "scripts/benchmark_extraction.py",
    "scripts/setup_local_env.sh",
    "benchmarks/receipt-extraction-v1.json",
}
REMOVED_FILES = {
    "contracts/trip-reimbursement-manifest.v2.schema.json",
    "contracts/trip-reimbursement-manifest.v3.schema.json",
    "nlp_expenses/expense_ids.py",
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", nargs="?", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)

    dist = args.dist.resolve()
    wheel = dist / f"nlp_expenses-{__version__}-py3-none-any.whl"
    sdist = dist / f"nlp_expenses-{__version__}.tar.gz"
    if not wheel.is_file() or not sdist.is_file():
        raise SystemExit(f"Build version {__version__} first with `python -m build`.")

    with zipfile.ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
    check_contents("wheel", wheel_names, WHEEL_ASSETS)

    with tarfile.open(sdist) as archive:
        sdist_names = {"/".join(name.split("/")[1:]) for name in archive.getnames() if "/" in name}
    check_contents("sdist", sdist_names, SDIST_ASSETS)
    print(
        f"Distribution contents OK: {len(wheel_names)} wheel entries, "
        f"{len(sdist_names)} sdist entries."
    )


def check_contents(label: str, names: set[str], required: set[str]) -> None:
    missing = sorted(required - names)
    stale = sorted(REMOVED_FILES & names)
    if missing or stale:
        problems = []
        if missing:
            problems.append(f"missing: {', '.join(missing)}")
        if stale:
            problems.append(f"stale: {', '.join(stale)}")
        raise SystemExit(f"Invalid {label} contents ({'; '.join(problems)}).")


if __name__ == "__main__":
    main()
