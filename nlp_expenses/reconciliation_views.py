from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from nlp_expenses.matching import (
    card_total_gap_percent,
    close_amount,
    close_fx_amount,
    days_between,
)
from nlp_expenses.reconciliation_allocations import REIMBURSABLE_ALLOCATION_TYPES
from nlp_expenses.reconciliation_coverage import statement_coverage_view
from nlp_expenses.reconciliation_state import (
    load_reconciliation_state,
    reconciliation_input_fingerprint,
)
from nlp_expenses.trip_metadata import trip_policy_warnings
from nlp_expenses.trips import trip_mode


def transaction_matches_expense(transaction: dict, source_file: str) -> bool:
    if transaction.get("ignored"):
        return False
    if transaction.get("expense_file") == source_file and not transaction.get("allocations"):
        return True
    return any(
        allocation.get("invoice_file") == source_file
        and allocation.get("type") in REIMBURSABLE_ALLOCATION_TYPES
        for allocation in transaction.get("allocations") or []
    )


def transaction_summary(transaction: dict) -> dict:
    return {
        "group_id": transaction.get("group_id"),
        "transaction_date": transaction.get("transaction_date"),
        "provider": transaction.get("provider"),
        "account_label": transaction.get("account_label"),
        "description": transaction.get("description"),
        "purchase_amount": transaction.get("purchase_amount"),
        "purchase_currency": transaction.get("purchase_currency"),
        "cad_amount": transaction.get("cad_amount"),
        "cad_completeness": transaction.get("cad_completeness"),
        "expense_file": transaction.get("expense_file"),
        "allocation_status": transaction.get("allocation_status"),
    }


def serialized_candidate_score(expense: dict, transaction: dict, estimated_cad: object) -> float:
    """Rank receipt-centric choices using date and the employee share's amount."""

    score = 0.0
    delta = days_between(expense.get("date"), transaction.get("transaction_date"))
    if delta == 0:
        score += 0.35
    elif delta is not None and delta <= 5:
        score += max(0.05, 0.28 - 0.05 * delta)
    amount = expense.get("amount")
    exact_amount = False
    if isinstance(amount, (int, float)):
        people = max(1, int(expense.get("number_of_people") or 1))
        share = abs(float(amount)) / people
        purchase_amount = transaction.get("purchase_amount")
        purchase_currency = str(transaction.get("purchase_currency") or "").upper()
        expense_currency = str(expense.get("currency") or "").upper()
        cad_amount = transaction.get("cad_amount")
        if (
            (
                isinstance(purchase_amount, (int, float))
                and purchase_currency == expense_currency
                and close_amount(share, abs(float(purchase_amount)))
            )
            or (
                isinstance(estimated_cad, (int, float))
                and isinstance(cad_amount, (int, float))
                and close_fx_amount(float(estimated_cad), abs(float(cad_amount)))
            )
            or (
                expense_currency == "CAD"
                and isinstance(cad_amount, (int, float))
                and close_amount(share, abs(float(cad_amount)))
            )
        ):
            score += 0.55
            exact_amount = True
        elif serialized_card_total_gap_percent(expense, transaction) is not None:
            score += 0.25
    vendor = str(expense.get("vendor") or "").lower()
    description = str(transaction.get("description") or "").lower()
    if vendor and description:
        score += 0.25 * SequenceMatcher(None, vendor, description).ratio()
        vendor_tokens = {token for token in vendor.split() if len(token) > 2}
        if vendor_tokens and any(token in description for token in vendor_tokens):
            score += 0.10
    if delta == 0 and exact_amount:
        score += 0.05
    return min(score, 1.0)


def serialized_card_total_gap_percent(expense: dict, transaction: dict) -> int | None:
    amount = expense.get("amount")
    if not isinstance(amount, (int, float)):
        return None
    people = max(1, int(expense.get("number_of_people") or 1))
    share = abs(float(amount)) / people
    expense_currency = str(expense.get("currency") or "").upper()
    purchase_currency = str(transaction.get("purchase_currency") or "").upper()
    purchase_amount = transaction.get("purchase_amount")
    if (
        expense_currency
        and expense_currency == purchase_currency
        and isinstance(purchase_amount, (int, float))
    ):
        return card_total_gap_percent(share, abs(float(purchase_amount)))
    cad_amount = transaction.get("cad_amount")
    if expense_currency == "CAD" and isinstance(cad_amount, (int, float)):
        return card_total_gap_percent(share, abs(float(cad_amount)))
    return None


def serialized_candidate_reason(expense: dict, transaction: dict) -> str:
    gap = serialized_card_total_gap_percent(expense, transaction)
    delta = days_between(expense.get("date"), transaction.get("transaction_date"))
    vendor = str(expense.get("vendor") or "").lower()
    description = str(transaction.get("description") or "").lower()
    merchant_similarity = (
        SequenceMatcher(None, vendor, description).ratio() if vendor and description else 0.0
    )
    if gap is not None and delta == 0 and merchant_similarity >= 0.35:
        return (
            f"Same merchant and date; receipt is {gap}% below the card total, possibly tax or tip."
        )
    if gap is not None:
        return f"Receipt is {gap}% below the card total; review tax or tip."
    if delta == 0:
        return "Same transaction date; amount and merchant also influence this ranking."
    return ""


def reconciliation_view(trip_dir: Path, state: dict | None = None) -> dict:
    state = state if state is not None else load_reconciliation_state(trip_dir)
    if not state:
        return {
            "available": False,
            "stale": False,
            "synced_at": None,
            "expenses": [],
            "transactions": [],
            "unmatched_expenses": [],
            "summary": {
                "invoice_count": 0,
                "matched_invoice_count": 0,
                "transaction_count": 0,
                "matched_transaction_count": 0,
                "needs_review_count": 0,
            },
        }

    expenses = [dict(expense) for expense in state.get("expenses", [])]
    expenses_by_file = {expense["source_file"]: expense for expense in expenses}
    invoice_overrides = state.get("invoice_overrides", {})
    cad_overrides = state.get("manual_cad_overrides", {})
    for expense in expenses:
        source_file = expense["source_file"]
        expense["overridden_fields"] = sorted(invoice_overrides.get(source_file, {}))
        expense["manual_cad_override"] = cad_overrides.get(source_file)
        expense["extraction_status"] = serialized_extraction_status(expense)
    manual_matches = state.get("manual_matches", {})
    transactions = []
    matched_files: set[str] = set()
    cad_totals_by_expense: dict[str, float] = defaultdict(float)
    purchase_totals_by_expense: dict[str, float] = defaultdict(float)
    purchase_currencies_by_expense: dict[str, set[str]] = defaultdict(set)
    incomplete_expenses: set[str] = set()
    allocation_expenses: set[str] = set()
    for stored in state.get("transactions", []):
        transaction = dict(stored)
        group_id = transaction["group_id"]
        transaction["override_active"] = group_id in manual_matches
        transaction["override_expense_file"] = manual_matches.get(group_id)
        allocations = transaction.get("allocations") or []
        if allocations and transaction.get("allocation_status") == "balanced":
            reimbursable_allocations = [
                allocation
                for allocation in allocations
                if allocation.get("invoice_file") in expenses_by_file
                and allocation.get("type") in REIMBURSABLE_ALLOCATION_TYPES
            ]
            for allocation in allocations:
                expense_file = allocation.get("invoice_file")
                if (
                    expense_file in expenses_by_file
                    and allocation.get("type") in REIMBURSABLE_ALLOCATION_TYPES
                ):
                    matched_files.add(expense_file)
                    allocation_expenses.add(expense_file)
                    cad_totals_by_expense[expense_file] += float(allocation.get("cad_amount") or 0)
                    original_amount = allocation.get("original_amount")
                    if (
                        not isinstance(original_amount, (int, float))
                        and len(reimbursable_allocations) == 1
                    ):
                        original_amount = transaction.get("purchase_amount")
                    purchase_currency = str(transaction.get("purchase_currency") or "").upper()
                    if (
                        isinstance(original_amount, (int, float))
                        and original_amount
                        and purchase_currency
                    ):
                        purchase_totals_by_expense[expense_file] += abs(float(original_amount))
                        purchase_currencies_by_expense[expense_file].add(purchase_currency)
        expense = (
            expenses_by_file.get(transaction.get("expense_file"))
            if not transaction.get("ignored") and not allocations
            else None
        )
        suggested = expenses_by_file.get(transaction.get("suggested_expense_file"))
        if expense:
            expense_file = expense["source_file"]
            matched_files.add(expense_file)
            if transaction.get("cad_completeness") == "complete" and isinstance(
                transaction.get("cad_amount"), (int, float)
            ):
                cad_totals_by_expense[expense_file] += transaction["cad_amount"]
                purchase_amount = transaction.get("purchase_amount")
                purchase_currency = str(transaction.get("purchase_currency") or "").upper()
                if (
                    isinstance(purchase_amount, (int, float))
                    and purchase_amount
                    and purchase_currency
                ):
                    purchase_totals_by_expense[expense_file] += abs(float(purchase_amount))
                    purchase_currencies_by_expense[expense_file].add(purchase_currency)
            else:
                incomplete_expenses.add(expense_file)
        transaction["expense_label"] = expense_label(expense) if expense else None
        transaction["suggested_expense_label"] = expense_label(suggested) if suggested else None
        transactions.append(transaction)

    for transaction in transactions:
        if transaction.get("allocations"):
            transaction["fx_rate"] = None
            transaction["cad_source"] = "allocation"
            continue
        expense_file = transaction.get("expense_file")
        expense = expenses_by_file.get(expense_file)
        complete = bool(expense_file) and expense_file not in incomplete_expenses
        override = cad_overrides.get(expense_file, {})
        manual_amount = override.get("amount")
        if isinstance(manual_amount, (int, float)):
            basis_amount, basis_status = accounting_basis(expense)
            transaction["fx_basis_amount_used"] = basis_amount
            transaction["fx_basis_status"] = f"manual_{basis_status}"
            transaction["fx_rate"] = accounting_rate(expense, manual_amount, True)
            transaction["cad_source"] = "manual"
        else:
            basis_amount, basis_status = transaction_accounting_basis(
                expense,
                transaction.get("purchase_amount"),
                transaction.get("purchase_currency"),
            )
            transaction["fx_basis_amount_used"] = basis_amount
            transaction["fx_basis_status"] = basis_status
            transaction["fx_rate"] = (
                round(abs(float(transaction.get("cad_amount") or 0.0)) / abs(basis_amount), 6)
                if complete and isinstance(basis_amount, (int, float)) and basis_amount
                else None
            )
            transaction["cad_source"] = "statement" if complete else "unavailable"

    needs_review = sum(
        1
        for transaction in transactions
        if transaction.get("match_eligible")
        and not transaction.get("ignored")
        and (
            (transaction.get("allocations") and transaction.get("allocation_status") != "balanced")
            or (not transaction.get("allocations") and not transaction.get("expense_file"))
            or transaction.get("normalization_status") in {"review", "possible_duplicate"}
            or (
                not transaction.get("allocations")
                and transaction.get("cad_completeness") != "complete"
                and transaction.get("cad_source") != "manual"
            )
        )
    )

    group_counts_by_expense: dict[str, int] = defaultdict(int)
    for transaction in transactions:
        if transaction.get("allocations") and transaction.get("allocation_status") == "balanced":
            for allocation in transaction["allocations"]:
                expense_file = allocation.get("invoice_file")
                if expense_file and allocation.get("type") in REIMBURSABLE_ALLOCATION_TYPES:
                    group_counts_by_expense[expense_file] += 1
        elif transaction.get("expense_file") and not transaction.get("ignored"):
            group_counts_by_expense[transaction["expense_file"]] += 1
    for expense in expenses:
        source_file = expense["source_file"]
        override = cad_overrides.get(source_file, {})
        manual_amount = override.get("amount")
        statement_complete = source_file in matched_files and source_file not in incomplete_expenses
        purchase_currencies = purchase_currencies_by_expense.get(source_file, set())
        statement_purchase_currency = (
            next(iter(purchase_currencies)) if len(purchase_currencies) == 1 else None
        )
        statement_purchase_amount = (
            round(purchase_totals_by_expense.get(source_file, 0.0), 6)
            if statement_purchase_currency
            else None
        )
        expense["statement_purchase_amount_used"] = statement_purchase_amount
        expense["statement_purchase_currency"] = statement_purchase_currency
        if isinstance(manual_amount, (int, float)):
            expense["cad_amount_used"] = manual_amount
            expense["cad_source"] = "manual"
            expense["cad_source_note"] = override.get("note", "")
        elif statement_complete:
            expense["cad_amount_used"] = round(cad_totals_by_expense.get(source_file, 0.0), 2)
            if source_file in allocation_expenses:
                expense["cad_source"] = (
                    "allocation_aggregated"
                    if group_counts_by_expense[source_file] > 1
                    else "allocation"
                )
            else:
                expense["cad_source"] = (
                    "statement_aggregated"
                    if group_counts_by_expense[source_file] > 1
                    else "statement"
                )
            expense["cad_source_note"] = ""
        elif expense.get("currency") == "CAD" and not group_counts_by_expense[source_file]:
            expense["cad_amount_used"] = expense.get("amount")
            expense["cad_source"] = "invoice"
            expense["cad_source_note"] = ""
        else:
            expense["cad_amount_used"] = None
            expense["cad_source"] = "unavailable"
            expense["cad_source_note"] = ""
        expense["fx_rate"] = accounting_rate(
            expense,
            expense.get("cad_amount_used") or 0.0,
            expense.get("cad_amount_used") is not None,
            (
                statement_purchase_amount
                if expense.get("cad_source")
                in {
                    "statement",
                    "statement_aggregated",
                    "allocation",
                    "allocation_aggregated",
                }
                else None
            ),
            statement_purchase_currency,
            aggregated=group_counts_by_expense[source_file] > 1,
        )
        expense["fx_basis_amount_used"], expense["fx_basis_status"] = accounting_basis(
            expense,
            (
                statement_purchase_amount
                if expense.get("cad_source")
                in {
                    "statement",
                    "statement_aggregated",
                    "allocation",
                    "allocation_aggregated",
                }
                else None
            ),
            statement_purchase_currency,
            aggregated=group_counts_by_expense[source_file] > 1,
        )
        if expense.get("cad_source") == "manual":
            expense["fx_basis_status"] = f"manual_{expense['fx_basis_status']}"

    estimated_cad_by_expense = state.get("estimated_cad_by_expense", {})
    for expense in expenses:
        source_file = expense["source_file"]
        expense["statement_matches"] = [
            transaction_summary(transaction)
            for transaction in transactions
            if transaction_matches_expense(transaction, source_file)
        ]
        candidates = []
        for transaction in transactions:
            if (
                not transaction.get("match_eligible")
                or transaction.get("ignored")
                or transaction.get("allocations")
            ):
                continue
            score = serialized_candidate_score(
                expense,
                transaction,
                estimated_cad_by_expense.get(source_file),
            )
            candidate = {
                "group_id": transaction.get("group_id"),
                "score": round(score, 3),
                "likely": score >= 0.35,
                "current": transaction.get("expense_file") == source_file,
                "suggested": transaction.get("suggested_expense_file") == source_file,
                "matched_elsewhere": bool(
                    transaction.get("expense_file")
                    and transaction.get("expense_file") != source_file
                ),
                "reason": serialized_candidate_reason(expense, transaction),
            }
            candidates.append(candidate)
        ranked = sorted(
            candidates,
            key=lambda item: (
                not item["current"],
                not item["suggested"],
                not item["likely"],
                -item["score"],
            ),
        )
        expense["match_suggestions"] = [
            item for item in ranked if item["current"] or item["suggested"] or item["likely"]
        ][:12]

    policy_warnings = trip_policy_warnings(trip_dir, expenses, transactions)
    unresolved_policy_warnings = sum(1 for warning in policy_warnings if not warning["resolved"])
    needs_review += unresolved_policy_warnings
    unmatched_expenses = [
        expense for expense in expenses if expense["source_file"] not in matched_files
    ]
    return {
        "available": True,
        "stale": not reconciliation_is_fresh(trip_dir, state),
        "synced_at": state.get("synced_at"),
        "expenses": expenses,
        "transactions": transactions,
        "unmatched_expenses": unmatched_expenses,
        "warnings": list(state.get("warnings", [])),
        "policy_warnings": policy_warnings,
        "coverage": statement_coverage_view(trip_dir, state),
        "summary": {
            "invoice_count": len(expenses),
            "matched_invoice_count": len(matched_files),
            "transaction_count": sum(
                1 for item in transactions if item.get("match_eligible") and not item.get("ignored")
            ),
            "matched_transaction_count": sum(
                1
                for item in transactions
                if item.get("match_eligible")
                and not item.get("ignored")
                and (
                    item.get("expense_file")
                    or (item.get("allocations") and item.get("allocation_status") == "balanced")
                )
            ),
            "audit_transaction_count": sum(
                1 for item in transactions if not item.get("match_eligible") or item.get("ignored")
            ),
            "incomplete_allocation_count": sum(
                1
                for item in transactions
                if item.get("allocations") and item.get("allocation_status") != "balanced"
            ),
            "unresolved_policy_warning_count": unresolved_policy_warnings,
            "needs_review_count": needs_review,
        },
    }


def reconciliation_is_fresh(trip_dir: Path, state: dict | None = None) -> bool:
    state = state if state is not None else load_reconciliation_state(trip_dir)
    return bool(
        state
        and state.get("mode") == trip_mode(trip_dir)
        and not state.get("requires_resync")
        and state.get("input_fingerprint") == reconciliation_input_fingerprint(trip_dir)
    )


def serialized_extraction_status(expense: dict) -> str:
    if expense.get("amount") is None or not expense.get("currency"):
        return "manual"
    if (
        not expense.get("date")
        or not expense.get("vendor")
        or expense.get("vendor") == "Unknown supplier"
    ):
        return "review"
    return "review" if expense.get("review_note") else "ok"


def expense_label(expense: dict | None) -> str | None:
    if not expense:
        return None
    vendor = expense.get("vendor") or expense.get("source_file") or "Unknown invoice"
    amount = expense.get("amount")
    currency = expense.get("currency") or ""
    amount_label = (
        f"{amount:,.2f} {currency}" if isinstance(amount, (int, float)) else "amount missing"
    )
    date = expense.get("date") or "date missing"
    return f"{vendor} · {amount_label} · {date}"


def accounting_rate(
    expense: dict | None,
    cad_amount: float,
    complete: bool,
    statement_purchase_amount: float | None = None,
    statement_purchase_currency: str | None = None,
    *,
    aggregated: bool = False,
) -> float | None:
    if not expense or not complete:
        return None
    amount, _status = accounting_basis(
        expense,
        statement_purchase_amount,
        statement_purchase_currency,
        aggregated=aggregated,
    )
    if not isinstance(amount, (int, float)) or not amount:
        return None
    return round(abs(cad_amount) / abs(amount), 6)


def accounting_basis(
    expense: dict | None,
    statement_purchase_amount: float | None = None,
    statement_purchase_currency: str | None = None,
    *,
    aggregated: bool = False,
) -> tuple[float | None, str]:
    """Choose an auditable original-currency denominator for an accounting FX rate.

    Some card exports contain a truncated or otherwise malformed foreign amount. A
    statement amount is therefore accepted only when it plausibly represents either
    the full receipt or the traveller's equal share of a multi-person receipt.
    """

    if not expense:
        return None, "unavailable"
    receipt_amount = expense.get("amount")
    if not isinstance(receipt_amount, (int, float)) or not receipt_amount:
        return None, "unavailable"

    receipt_amount = abs(float(receipt_amount))
    expense_currency = str(expense.get("currency") or "").upper()
    statement_currency = str(statement_purchase_currency or "").upper()
    if not isinstance(statement_purchase_amount, (int, float)) or not statement_purchase_amount:
        return receipt_amount, "receipt_total"
    if not statement_currency or statement_currency != expense_currency:
        return receipt_amount, "receipt_fallback_currency"

    statement_amount = abs(float(statement_purchase_amount))
    if aggregated:
        return statement_amount, "statement_aggregated"
    people = max(1, int(expense.get("number_of_people") or 1))
    if amounts_align(statement_amount, receipt_amount):
        return statement_amount, "statement_receipt_total"
    if people > 1 and amounts_align(statement_amount, receipt_amount / people):
        return statement_amount, "statement_person_share"
    return receipt_amount, "receipt_fallback_mismatch"


def transaction_accounting_basis(
    expense: dict | None,
    statement_purchase_amount: float | None,
    statement_purchase_currency: str | None,
) -> tuple[float | None, str]:
    """Use a card row's own original amount for its row-level FX display.

    The final expense calculation still applies ``accounting_basis`` sanity
    checks. Keeping row-level FX independent makes a mistaken invoice mapping
    visible instead of distorting the card transaction's observed rate.
    """

    expense_currency = str((expense or {}).get("currency") or "").upper()
    statement_currency = str(statement_purchase_currency or "").upper()
    if (
        isinstance(statement_purchase_amount, (int, float))
        and statement_purchase_amount
        and statement_currency
        and statement_currency == expense_currency
    ):
        return abs(float(statement_purchase_amount)), "statement_transaction"
    return accounting_basis(expense, statement_purchase_amount, statement_purchase_currency)


def amounts_align(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= max(2.0, abs(expected) * 0.08)
