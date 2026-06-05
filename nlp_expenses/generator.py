from __future__ import annotations

from pathlib import Path

from nlp_expenses.config import ask_openai_for_run, get_openai_settings, prompt_for_openai_if_missing
from nlp_expenses.extraction.receipts import parse_receipt
from nlp_expenses.extraction.statements import parse_all_statements
from nlp_expenses.models import Expense
from nlp_expenses.trips import trip_receipts_dir, trip_statements_dir
from nlp_expenses.workbook import build_workbook


SUPPORTED_RECEIPTS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".heic"}


def generate_review(trip_dir: Path, root: Path, llm_mode: str = "ask") -> Path:
    receipts_dir = trip_receipts_dir(trip_dir)
    statements_dir = trip_statements_dir(trip_dir)
    if not receipts_dir.exists():
        raise FileNotFoundError(f"Missing receipts folder: {receipts_dir}")
    if not statements_dir.exists():
        statements_dir.mkdir(parents=True, exist_ok=True)

    if llm_mode == "ask" or llm_mode == "auto":
        api_key, model = ask_openai_for_run(root)
    elif llm_mode == "required":
        api_key, model = prompt_for_openai_if_missing(root)
    else:
        api_key, model = get_openai_settings(root)
    use_llm = llm_mode != "off" and bool(api_key)

    receipt_files = sorted(p for p in receipts_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_RECEIPTS)
    force_llm = llm_mode == "required" or (llm_mode in {"ask", "auto"} and bool(api_key))
    expenses = [parse_receipt(path, use_llm=use_llm, model=model, force_llm=force_llm) for path in receipt_files]
    assign_simple_expense_ids(expenses)
    transactions = parse_all_statements(statements_dir)
    return build_workbook(trip_dir, expenses, transactions)


def assign_simple_expense_ids(expenses: list[Expense]) -> None:
    for idx, expense in enumerate(expenses, start=1):
        date_part = expense.date.replace("-", "") if expense.date else "yyyymmdd"
        expense.expense_id = f"{date_part}_#{idx}"
