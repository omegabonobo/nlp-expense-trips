from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
import re

from nlp_expenses.models import Expense, NormalizedTransaction, StatementTransaction
from nlp_expenses.trips import source_file_key


def match_transactions(expenses: list[Expense], transactions: list[StatementTransaction]) -> None:
    for transaction in transactions:
        best_expense: Expense | None = None
        best_score = 0.0
        best_review_reason = ""
        for expense in expenses:
            score = match_score(expense, transaction)
            if score > best_score:
                best_score = score
                best_expense = expense
                best_review_reason = statement_match_review_reason(expense, transaction)
        if best_expense and best_score >= 0.35:
            transaction.suggested_expense_id = best_expense.expense_id
            transaction.match_confidence = round(best_score, 3)
            if best_score >= 0.72 and not best_review_reason:
                transaction.expense_id = best_expense.expense_id
    enrich_expenses_from_statements(expenses, transactions)


def match_normalized_transactions(
    expenses: list[Expense],
    transactions: list[NormalizedTransaction],
    estimated_cad_by_expense: dict[str, float] | None = None,
) -> None:
    groups: dict[str, list[NormalizedTransaction]] = defaultdict(list)
    for transaction in transactions:
        groups[transaction.transaction_group_id].append(transaction)

    enrichment_rows: list[StatementTransaction] = []
    for legs in groups.values():
        eligible = [leg for leg in legs if leg.match_eligible]
        if not eligible:
            continue
        representative = eligible[0]
        purchase_currency = common_value([leg.purchase_currency for leg in eligible])
        purchase_amount = sum_values([leg.purchase_amount for leg in eligible]) if purchase_currency else None
        cad_complete = all(leg.cad_completeness == "complete" for leg in eligible)
        cad_amount = sum_values([leg.cad_amount for leg in eligible]) if cad_complete else None

        best_expense: Expense | None = None
        best_score = 0.0
        best_review_reason = ""
        for expense in expenses:
            score = normalized_match_score(
                expense,
                representative.transaction_date,
                representative.description,
                purchase_amount,
                purchase_currency,
                cad_amount,
                (estimated_cad_by_expense or {}).get(source_file_key(expense.source_file)),
            )
            if score > best_score:
                best_score = score
                best_expense = expense
                best_review_reason = normalized_match_review_reason(
                    expense,
                    representative.transaction_date,
                    representative.description,
                    purchase_amount,
                    purchase_currency,
                    cad_amount,
                )

        if not best_expense or best_score < 0.35:
            continue
        confidence = round(best_score, 3)
        auto_assign = best_score >= 0.72 and not best_review_reason and all(
            leg.normalization_status in {"ok", "duplicate_confirmed"}
            for leg in eligible
        )
        for leg in legs:
            leg.suggested_expense_id = best_expense.expense_id
            leg.match_confidence = confidence
            leg.match_status = "suggested"
            leg.match_review_reason = best_review_reason
            if auto_assign:
                leg.expense_id = best_expense.expense_id
                leg.match_status = "auto"
        enrichment_rows.append(
            StatementTransaction(
                source_file=representative.source_file,
                date=representative.transaction_date,
                description=representative.description,
                amount_cad=cad_amount,
                foreign_amount=purchase_amount,
                foreign_currency=purchase_currency,
                expense_id=best_expense.expense_id if auto_assign else "",
                suggested_expense_id=best_expense.expense_id,
                match_confidence=confidence,
            )
        )
    enrich_expenses_from_statements(expenses, enrichment_rows)
    for expense in expenses:
        if expense.expense_type and expense.expense_type.startswith("meal"):
            expense.expense_type = "meal"


def apply_manual_matches(
    expenses: list[Expense],
    transactions: list[NormalizedTransaction],
    manual_matches: dict[str, str | None],
) -> None:
    """Apply persisted transaction-group overrides after automatic matching.

    Values are receipt filenames rather than generated expense IDs so mappings
    remain stable when receipt dates or list ordering change between runs.
    A present key with a ``None`` value is an explicit manual unmatch.
    """

    expenses_by_file = {source_file_key(expense.source_file): expense for expense in expenses}
    groups: dict[str, list[NormalizedTransaction]] = defaultdict(list)
    for transaction in transactions:
        groups[transaction.transaction_group_id].append(transaction)

    for group_id, receipt_file in manual_matches.items():
        legs = groups.get(group_id)
        if not legs:
            continue
        expense = expenses_by_file.get(receipt_file) if receipt_file else None
        if receipt_file and not expense:
            continue
        for leg in legs:
            if not leg.match_eligible:
                continue
            if expense:
                leg.expense_id = expense.expense_id
                leg.suggested_expense_id = expense.expense_id
                leg.match_status = "manual"
                leg.match_confidence = 1.0
                leg.match_review_reason = ""
            else:
                leg.expense_id = ""
                leg.match_status = "unmatched"
                leg.match_review_reason = ""


def normalized_match_score(
    expense: Expense,
    transaction_date: str | None,
    description: str,
    purchase_amount: float | None,
    purchase_currency: str | None,
    cad_amount: float | None,
    estimated_expense_cad: float | None = None,
) -> float:
    score = 0.0
    date_delta = days_between(expense.date, transaction_date)
    if date_delta is not None:
        if date_delta == 0:
            score += 0.3
        elif date_delta <= 3:
            score += max(0.0, 0.24 - 0.05 * date_delta)
    receipt_amount = shared_receipt_amount(expense)
    exact_amount = False
    if receipt_amount is not None:
        if (
            purchase_amount is not None
            and expense.currency == purchase_currency
            and close_amount(receipt_amount, abs(purchase_amount))
        ):
            score += 0.36
            exact_amount = True
        elif expense.currency == "CAD" and cad_amount is not None and close_amount(receipt_amount, abs(cad_amount)):
            score += 0.36
            exact_amount = True
        elif (
            estimated_expense_cad is not None
            and cad_amount is not None
            and close_fx_amount(estimated_expense_cad, abs(cad_amount))
        ):
            score += 0.46
            exact_amount = True
        elif cad_amount is not None and close_amount(receipt_amount, abs(cad_amount)):
            score += 0.18
            exact_amount = True
        elif normalized_card_total_gap_percent(
            expense,
            purchase_amount,
            purchase_currency,
            cad_amount,
        ) is not None:
            score += 0.22
    if date_delta == 0 and exact_amount:
        score += 0.08
    supplier = (expense.supplier_name or "").lower()
    normalized_description = description.lower()
    if supplier and normalized_description:
        score += 0.34 * SequenceMatcher(None, supplier, normalized_description).ratio()
        supplier_tokens = {token for token in supplier.split() if len(token) > 2}
        if supplier_tokens and any(token in normalized_description for token in supplier_tokens):
            score += 0.12
    return min(score, 1.0)


def common_value(values: list[str | None]) -> str | None:
    present = {value for value in values if value}
    return next(iter(present)) if len(present) == 1 else None


def sum_values(values: list[float | None]) -> float | None:
    if not values or any(value is None for value in values):
        return None
    return round(sum(value or 0.0 for value in values), 2)


def match_score(expense: Expense, transaction: StatementTransaction) -> float:
    score = 0.0
    date_delta = days_between(expense.date, transaction.date)
    if date_delta is not None:
        if date_delta == 0:
            score += 0.3
        elif date_delta <= 3:
            score += max(0.0, 0.24 - 0.05 * date_delta)
    receipt_amount = shared_receipt_amount(expense)
    exact_amount = False
    if receipt_amount is not None:
        if transaction.foreign_amount is not None and close_amount(receipt_amount, transaction.foreign_amount):
            score += 0.36
            exact_amount = True
        elif transaction.foreign_amount is not None and close_split_amount(receipt_amount, transaction.foreign_amount):
            score += 0.28
        elif expense.currency == "CAD" and transaction.amount_cad is not None and close_amount(receipt_amount, transaction.amount_cad):
            score += 0.36
            exact_amount = True
        elif expense.currency == "CAD" and transaction.amount_cad is not None and close_split_amount(receipt_amount, transaction.amount_cad):
            score += 0.28
        elif transaction.amount_cad is not None and close_amount(receipt_amount, transaction.amount_cad):
            score += 0.18
            exact_amount = True
        elif statement_card_total_gap_percent(expense, transaction) is not None:
            score += 0.22
    if date_delta == 0 and exact_amount:
        score += 0.08
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


def close_fx_amount(a: float, b: float) -> bool:
    """Allow normal card spread while comparing a weekly FX estimate to settled CAD."""

    return abs(a - b) <= max(0.50, abs(a) * 0.06)


def card_total_gap_percent(receipt_amount: float | None, card_amount: float | None) -> int | None:
    """Return a plausible tax/tip uplift when the card total exceeds the receipt amount."""

    if receipt_amount is None or card_amount is None:
        return None
    receipt = abs(float(receipt_amount))
    card = abs(float(card_amount))
    if receipt <= 0 or card <= receipt:
        return None
    receipt_share_of_card = receipt / card
    if receipt_share_of_card < 0.68 or receipt_share_of_card > 0.95:
        return None
    return round((1 - receipt / card) * 100)


def normalized_card_total_gap_percent(
    expense: Expense,
    purchase_amount: float | None,
    purchase_currency: str | None,
    cad_amount: float | None,
) -> int | None:
    receipt_amount = shared_receipt_amount(expense)
    expense_currency = str(expense.currency or "").upper()
    if expense_currency and expense_currency == str(purchase_currency or "").upper():
        return card_total_gap_percent(receipt_amount, purchase_amount)
    if expense_currency == "CAD":
        return card_total_gap_percent(receipt_amount, cad_amount)
    return None


def normalized_match_review_reason(
    expense: Expense,
    transaction_date: str | None,
    description: str,
    purchase_amount: float | None,
    purchase_currency: str | None,
    cad_amount: float | None,
) -> str:
    gap = normalized_card_total_gap_percent(expense, purchase_amount, purchase_currency, cad_amount)
    if gap is None:
        return ""
    date_delta = days_between(expense.date, transaction_date)
    supplier = (expense.supplier_name or "").lower()
    merchant_similarity = SequenceMatcher(None, supplier, (description or "").lower()).ratio()
    if date_delta == 0 and merchant_similarity >= 0.35:
        return f"Same merchant and date; receipt is {gap}% below the card total, possibly tax or tip."
    return f"Receipt is {gap}% below the card total; review tax or tip."


def statement_card_total_gap_percent(
    expense: Expense,
    transaction: StatementTransaction,
) -> int | None:
    receipt_amount = shared_receipt_amount(expense)
    if transaction.foreign_amount is not None and str(expense.currency or "").upper() == str(transaction.foreign_currency or "").upper():
        return card_total_gap_percent(receipt_amount, transaction.foreign_amount)
    if str(expense.currency or "").upper() == "CAD":
        return card_total_gap_percent(receipt_amount, transaction.amount_cad)
    return None


def statement_match_review_reason(expense: Expense, transaction: StatementTransaction) -> str:
    gap = statement_card_total_gap_percent(expense, transaction)
    return f"Receipt is {gap}% below the card total; review tax or tip." if gap is not None else ""


def shared_receipt_amount(expense: Expense) -> float | None:
    if expense.amount is None:
        return None
    people = max(1, int(expense.number_of_people or 1))
    return round(abs(float(expense.amount)) / people, 6)


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
