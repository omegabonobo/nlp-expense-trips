from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from nlp_expenses import __version__
from nlp_expenses.config import configure_openai
from nlp_expenses.generator import generate_review
from nlp_expenses.trips import (
    TRIP_MODES,
    ensure_trip,
    list_trips,
    normalize_trip_mode,
    trip_mode,
    validate_trip_name,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nlp-expenses",
        description="Generate local trip expense review workbooks.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("NLP_EXPENSES_ROOT", Path.cwd())),
        help="Data directory containing trips and local settings (default: current directory).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    create = sub.add_parser(
        "create-trip", help="Create a trip folder with receipt and statement subfolders."
    )
    create.add_argument("name", help="Trip folder name, e.g. 202606_melbourne")
    create.add_argument(
        "--mode",
        type=normalize_trip_mode,
        choices=sorted(TRIP_MODES),
        default="ivado",
        help="Trip processing mode.",
    )

    generate = sub.add_parser(
        "generate", help="Generate expense_review_[trip].xlsx for a trip folder."
    )
    generate.add_argument("trip", type=Path, help="Path to a trips/YYYYMM_tripName folder.")
    generate.add_argument(
        "--llm",
        choices=["ask", "off", "required"],
        default="ask",
        help="ask prompts whether to use an OpenAI API key; off uses heuristics only; required forces OpenAI extraction.",
    )
    generate.add_argument(
        "--mode",
        type=normalize_trip_mode,
        choices=sorted(TRIP_MODES),
        help="Override the trip's saved processing mode.",
    )
    generate.add_argument(
        "--statements-complete",
        action="store_true",
        help="Confirm non-interactively that all card/bank statement exports have been added.",
    )

    reconcile = sub.add_parser(
        "reconcile",
        help="Extract and sync a company trip's invoices with its current statement files.",
    )
    reconcile.add_argument("trip", type=Path, help="Path to a saved company trip folder.")
    reconcile.add_argument(
        "--llm",
        choices=["ask", "off", "required"],
        default="ask",
        help="Choose OpenAI-assisted or local-only invoice extraction.",
    )

    sub.add_parser(
        "configure-openai", help="Store OPENAI_API_KEY in local .env for fallback extraction."
    )

    ui = sub.add_parser("ui", help="Run the local browser interface.")
    ui.add_argument(
        "--port", type=int, default=8765, help="Preferred localhost port (default: 8765)."
    )
    ui.add_argument(
        "--no-browser", action="store_true", help="Do not open the browser automatically."
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()

    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()

    if args.command == "create-trip":
        trip = ensure_trip(root, args.name, mode=args.mode)
        print(f"Created {trip}")
        return
    if args.command == "generate":
        trip = args.trip.resolve()
        if not validate_trip_name(trip.name):
            raise SystemExit("Trip folder must be named like YYYYMM_tripName.")
        selected_mode = args.mode or trip_mode(trip)
        if selected_mode == "company" and not args.statements_complete and not sys.stdin.isatty():
            raise SystemExit("Non-interactive company generation requires --statements-complete.")
        output = generate_review(
            trip,
            root,
            llm_mode=args.llm,
            mode=args.mode,
            statements_complete=args.statements_complete,
        )
        if output:
            print(f"Generated {output}")
        else:
            print("Generation cancelled; no workbook was created or overwritten.")
        return
    if args.command == "reconcile":
        from nlp_expenses.reconciliation import sync_reconciliation

        trip = args.trip.resolve()
        if not validate_trip_name(trip.name):
            raise SystemExit("Trip folder must be named like YYYYMM_tripName.")
        result = sync_reconciliation(trip, root, llm_mode=args.llm)
        summary = result["summary"]
        print(
            "Reconciliation ready: "
            f"{summary['matched_invoice_count']}/{summary['invoice_count']} invoices matched; "
            f"{summary['needs_review_count']} items need review."
        )
        return
    if args.command == "configure-openai":
        configure_openai(root)
        print("Saved OpenAI settings to .env")
        return
    if args.command == "ui":
        from nlp_expenses.ui import run_local_ui

        run_local_ui(root, port=args.port, open_browser=not args.no_browser)
        return

    if sys.stdin.isatty():
        interactive_menu(root)
    else:
        parser.print_help()


def interactive_menu(root: Path) -> None:
    print("NLP Expenses")
    print("1. Create a new trip")
    print("2. Generate workbook for an existing trip")
    choice = input("Choose 1 or 2: ").strip()
    if choice == "1":
        name = input("Trip name (YYYYMM_tripName): ").strip()
        mode = input("Mode [ivado/company] (default ivado): ").strip().lower() or "ivado"
        trip = ensure_trip(root, name, mode=mode)
        print(f"Created {trip}")
        return
    if choice == "2":
        trips = list_trips(root)
        if not trips:
            print("No trips found under trips/.")
            return
        for idx, trip in enumerate(trips, start=1):
            print(f"{idx}. {trip.name}")
        selected = input("Trip number: ").strip()
        try:
            trip = trips[int(selected) - 1]
        except Exception as exc:
            raise SystemExit("Invalid trip selection.") from exc
        output = generate_review(trip, root)
        if output:
            print(f"Generated {output}")
        else:
            print("Generation cancelled; no workbook was created or overwritten.")
        return
    raise SystemExit("Invalid choice.")
