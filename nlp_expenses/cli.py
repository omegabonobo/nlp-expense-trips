from __future__ import annotations

import argparse
from pathlib import Path

from nlp_expenses.config import configure_openai
from nlp_expenses.generator import generate_review
from nlp_expenses.trips import ensure_trip, list_trips, validate_trip_name


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate local trip expense review workbooks.")
    sub = parser.add_subparsers(dest="command")

    create = sub.add_parser("create-trip", help="Create a trip folder with receipt and statement subfolders.")
    create.add_argument("name", help="Trip folder name, e.g. 202606_melbourne")

    generate = sub.add_parser("generate", help="Generate expense_review_[trip].xlsx for a trip folder.")
    generate.add_argument("trip", type=Path, help="Path to a trips/YYYYMM_tripName folder.")
    generate.add_argument(
        "--llm",
        choices=["ask", "off", "required"],
        default="ask",
        help="ask prompts whether to use an OpenAI API key; off uses heuristics only; required forces OpenAI extraction.",
    )

    sub.add_parser("configure-openai", help="Store OPENAI_API_KEY in local .env for fallback extraction.")

    args = parser.parse_args(argv)
    root = Path.cwd()

    if args.command == "create-trip":
        trip = ensure_trip(root, args.name)
        print(f"Created {trip}")
        return
    if args.command == "generate":
        trip = args.trip.resolve()
        if not validate_trip_name(trip.name):
            raise SystemExit("Trip folder must be named like YYYYMM_tripName.")
        output = generate_review(trip, root, llm_mode=args.llm)
        print(f"Generated {output}")
        return
    if args.command == "configure-openai":
        configure_openai(root)
        print("Saved OpenAI settings to .env")
        return

    interactive_menu(root)


def interactive_menu(root: Path) -> None:
    print("NLP Expenses")
    print("1. Create a new trip")
    print("2. Generate workbook for an existing trip")
    choice = input("Choose 1 or 2: ").strip()
    if choice == "1":
        name = input("Trip name (YYYYMM_tripName): ").strip()
        trip = ensure_trip(root, name)
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
        print(f"Generated {output}")
        return
    raise SystemExit("Invalid choice.")
