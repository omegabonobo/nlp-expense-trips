from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
import re

from nlp_expenses.models import Expense, StatementTransaction


def match_transactions(expenses: list[Expense], transactions: list[StatementTransaction]) -> None:
    for transaction in transactions:
        best_expense: Expense | None = None
        best_score = 0.0
        for expense in expenses:
            score = match_score(expense, transaction)
            if score > best_score:
                best_score = score
                best_expense = expense
        if best_expense and best_score >= 0.35:
            transaction.suggested_expense_id = best_expense.expense_id
            transaction.match_confidence = round(best_score, 3)
            if best_score >= 0.72:
                transaction.expense_id = best_expense.expense_id
    enrich_expenses_from_statements(expenses, transactions)


def match_score(expense: Expense, transaction: StatementTransaction) -> float:
    score = 0.0
    date_delta = days_between(expense.date, transaction.date)
    if date_delta is not None:
        if date_delta == 0:
            score += 0.3
        elif date_delta <= 3:
            score += max(0.0, 0.24 - 0.05 * date_delta)
    if expense.amount is not None:
        if transaction.foreign_amount is not None and close_amount(expense.amount, transaction.foreign_amount):
            score += 0.36
        elif transaction.foreign_amount is not None and close_split_amount(expense.amount, transaction.foreign_amount):
            score += 0.28
        elif expense.currency == "CAD" and transaction.amount_cad is not None and close_amount(expense.amount, transaction.amount_cad):
            score += 0.36
        elif expense.currency == "CAD" and transaction.amount_cad is not None and close_split_amount(expense.amount, transaction.amount_cad):
            score += 0.28
        elif transaction.amount_cad is not None and close_amount(expense.amount, transaction.amount_cad):
            score += 0.18
    supplier = (expense.supplier_name or "").lower()
    description = transaction.description.lower()
    if supplier and description:
        score += 0.34 * SequenceMatcher(None, supplier, description).ratio()
        supplier_tokens = {t for t in supplier.split() if len(t) > 2}
        if supplier_tokens and any(token in description for token in supplier_tokens):
            score += 0.12
    return min(score, 1.0)


def enrich_expenses_from_statements(expenses: list[Expense], transactions: list[StatementTransaction]) -> None:
    by_id = {expense.expense_id: expense for expense in expenses}
    for transaction in transactions:
        expense_id = transaction.expense_id or transaction.suggested_expense_id
        if not expense_id or transaction.match_confidence < 0.55:
            continue
        expense = by_id.get(expense_id)
        if not expense:
            continue
        merchant = merchant_from_statement(transaction.description)
        if merchant and should_replace_supplier(expense.supplier_name):
            expense.supplier_name = merchant
            expense.review_note = append_note(expense.review_note, f"Supplier inferred from card statement: {transaction.description}.")
        if merchant and is_food_merchant(merchant, transaction.description):
            expense.expense_type = infer_meal_type(expense.expense_type, expense.raw_text, merchant)


def merchant_from_statement(description: str) -> str:
    text = re.sub(r"\s+", " ", description or "").strip()
    text = re.sub(r"^[A-Z]{2,4}\*", "", text)
    text = re.sub(r"\b(MELBOURNE|HAWTHORN EAST|WANTIRNA|SYDNEY|LONDON|JAKARTA BARAT|HELP\.UBER\.COM)\b.*$", "", text, flags=re.I)
    text = text.strip(" -")
    known = {
        "FARMERS DAUGHTER": "Farmers Daughters",
        "NIGEL": "Nigel",
        "REINE": "Reine & La Rue",
        "GABRIEL": "Gabriel",
        "INTERMISSION": "Intermission",
    }
    upper = text.upper()
    for needle, clean in known.items():
        if needle in upper:
            return clean
    return title_merchant(text)


def should_replace_supplier(supplier: str | None) -> bool:
    if not supplier:
        return True
    supplier_l = supplier.lower()
    if supplier_l in {"tax ywvol", "tax invoice", "unknown supplier"}:
        return True
    return bool(re.search(r"\b(tax|invoice|receipt|scanned)\b", supplier_l))


def is_food_merchant(merchant: str, description: str) -> bool:
    blob = f"{merchant} {description}".lower()
    return any(term in blob for term in ["farmers", "nigel", "reine", "gabriel", "intermission", "espresso", "cafe", "restaurant", "hanks"])


def infer_meal_type(current: str | None, raw_text: str, merchant: str) -> str:
    if current and current.startswith("meal"):
        return current
    blob = f"{raw_text} {merchant}".lower()
    if any(term in blob for term in ["breakfast", "cappuccino", "benedict", "banana bread", "espresso"]):
        return "meal-breakfast"
    return "meal-dinner"


def title_merchant(value: str) -> str:
    if not value:
        return ""
    words = []
    for word in value.split():
        if word in {"&"}:
            words.append(word)
        else:
            words.append(word[:1].upper() + word[1:].lower())
    return " ".join(words)


def append_note(existing: str, note: str) -> str:
    return f"{existing} {note}".strip() if existing else note


def close_amount(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.03, abs(a) * 0.015)


def close_split_amount(receipt_amount: float, statement_amount: float) -> bool:
    if statement_amount <= 0 or receipt_amount <= statement_amount:
        return False
    ratio = receipt_amount / statement_amount
    nearest_person_count = round(ratio)
    if nearest_person_count < 2 or nearest_person_count > 12:
        return False
    return abs(ratio - nearest_person_count) <= 0.08


def days_between(left: str | None, right: str | None) -> int | None:
    if not left or not right:
        return None
    try:
        return abs((datetime.fromisoformat(left) - datetime.fromisoformat(right)).days)
    except ValueError:
        return None
