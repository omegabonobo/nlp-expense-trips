from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from nlp_expenses.extraction.statements import parse_statement_file
from nlp_expenses.fx_rates import FxRateUnavailable, WeeklyCadFxResolver
from nlp_expenses.generator import (
    ProgressCallback,
    WarningCallback,
    assign_simple_expense_ids,
    extract_trip_expenses,
)
from nlp_expenses.line_items import apply_line_item_review, save_line_item_review
from nlp_expenses.matching import (
    apply_manual_matches,
    close_amount,
    common_value,
    days_between,
    match_normalized_transactions,
    sum_values,
)
from nlp_expenses.models import (
    Expense,
    GenerationProgress,
    NormalizationResult,
    NormalizedTransaction,
    StatementFileReport,
    StatementTransaction,
)
from nlp_expenses.reconciliation_allocations import (
    apply_allocations_to_group,
    apply_allocations_to_groups,
    normalize_allocations,
    restore_group_before_allocations,
)
from nlp_expenses.reconciliation_coverage import statement_coverage_view
from nlp_expenses.reconciliation_invoices import (
    InvoiceValidationError as InvoiceValidationError,
)
from nlp_expenses.reconciliation_invoices import (
    apply_invoice_overrides,
    apply_manual_cad_overrides,
    apply_overrides_to_snapshots,
    invoice_values_differ,
    validate_invoice_fields,
    validate_manual_cad,
)
from nlp_expenses.reconciliation_state import (
    RECONCILIATION_VERSION,
    deserialize_manual_matches,
    deserialize_transaction_allocations,
    deserialize_transaction_decisions,
    load_invoice_overrides,
    load_manual_cad_overrides,
    load_reconciliation_state,
    reconciliation_input_fingerprint,
    save_reconciliation_state,
)
from nlp_expenses.reconciliation_views import (
    accounting_basis as accounting_basis,
)
from nlp_expenses.reconciliation_views import (
    reconciliation_is_fresh,
    reconciliation_view,
)
from nlp_expenses.reconciliation_views import (
    serialized_candidate_reason as serialized_candidate_reason,
)
from nlp_expenses.reconciliation_views import (
    serialized_candidate_score as serialized_candidate_score,
)
from nlp_expenses.statement_normalizer import (
    StatementNormalizationError,
    list_statement_files,
    normalize_statement_files,
    preflight_statement_files,
)
from nlp_expenses.trip_metadata import (
    apply_trip_metadata_defaults,
)
from nlp_expenses.trips import (
    load_trip_config,
    save_trip_config,
    source_file_key,
    trip_mode,
    trip_receipts_dir,
    trip_statements_dir,
)


def sync_reconciliation(
    trip_dir: Path,
    root: Path,
    llm_mode: str = "off",
    progress_callback: ProgressCallback | None = None,
    warning_callback: WarningCallback | None = None,
    allow_openai_prompt: bool = True,
) -> dict:
    """Extract invoices, normalize statements, auto-match, and persist a review snapshot."""

    trip_dir = trip_dir.resolve()
    root = root.resolve()
    selected_mode = trip_mode(trip_dir)
    statements = reconciliation_statement_files(trip_dir, selected_mode)
    fx_resolver = WeeklyCadFxResolver(trip_dir)
    emit_progress(
        progress_callback, "statements", 0, len(statements), "Validating card and bank statements"
    )
    if selected_mode == "arvine":
        reports = preflight_statement_files(statements)
        errors = [error for report in reports for error in report.errors]
        if errors:
            raise StatementNormalizationError("\n".join(errors))
        normalization = normalize_statement_files(
            statements,
            fx_resolver=fx_resolver,
        )
        if normalization.errors:
            raise StatementNormalizationError("\n".join(normalization.errors))
    else:
        normalization = normalize_ivado_statement_files(statements)
    if warning_callback:
        for message in normalization.warnings:
            warning_callback(message)
    emit_progress(
        progress_callback, "statements", len(statements), len(statements), "Statements normalized"
    )

    previous_state = load_reconciliation_state(trip_dir)
    current_fingerprint = reconciliation_input_fingerprint(trip_dir)
    invoice_overrides = load_invoice_overrides(trip_dir)
    manual_cad_overrides = load_manual_cad_overrides(trip_dir)
    transaction_decisions = (
        deserialize_transaction_decisions(previous_state)
        if previous_state and previous_state.get("input_fingerprint") == current_fingerprint
        else {}
    )
    transaction_allocations = (
        deserialize_transaction_allocations(previous_state)
        if previous_state and previous_state.get("input_fingerprint") == current_fingerprint
        else {}
    )
    expenses = extract_trip_expenses(
        trip_receipts_dir(trip_dir),
        root,
        selected_mode,
        llm_mode,
        progress_callback=progress_callback,
        warning_callback=warning_callback,
        allow_openai_prompt=allow_openai_prompt,
    )
    apply_trip_metadata_defaults(trip_dir, expenses)
    extracted_expenses = [serialize_expense(expense) for expense in expenses]
    save_line_item_review(trip_dir, expenses, llm_mode=llm_mode)
    apply_line_item_review(trip_dir, expenses, require_fresh=True)
    apply_invoice_overrides(expenses, invoice_overrides)
    apply_manual_cad_overrides(expenses, manual_cad_overrides)
    manual_cad_overrides = {
        source_file_key(expense.source_file): {
            "amount": expense.manual_cad_override,
            "note": expense.manual_cad_note,
        }
        for expense in expenses
        if expense.manual_cad_override is not None
    }
    assign_simple_expense_ids(expenses)
    emit_progress(
        progress_callback, "matching", 0, 1, "Matching invoices to statement transactions"
    )
    estimated_cad_by_expense = estimate_expense_cad_amounts(
        expenses,
        normalization.transactions,
        fx_resolver,
        warning_callback,
    )
    match_normalized_transactions(
        expenses,
        normalization.transactions,
        estimated_cad_by_expense=estimated_cad_by_expense,
    )

    expense_files = {source_file_key(expense.source_file) for expense in expenses}
    transaction_groups = {
        transaction.transaction_group_id for transaction in normalization.transactions
    }
    preserved_manual_matches = (
        deserialize_manual_matches(previous_state)
        if previous_state and previous_state.get("input_fingerprint") == current_fingerprint
        else {}
    )
    manual_matches = {
        group_id: receipt_file
        for group_id, receipt_file in preserved_manual_matches.items()
        if group_id in transaction_groups
        and (receipt_file is None or receipt_file in expense_files)
    }
    auto_groups = aggregate_transaction_groups(expenses, normalization.transactions)
    apply_manual_matches(expenses, normalization.transactions, manual_matches)
    current_groups = aggregate_transaction_groups(expenses, normalization.transactions)
    auto_by_id = {group["group_id"]: group for group in auto_groups}
    for group in current_groups:
        automatic = auto_by_id[group["group_id"]]
        group["auto_expense_file"] = automatic["expense_file"]
        group["auto_match_status"] = automatic["match_status"]
        group["auto_match_confidence"] = automatic["match_confidence"]
        group["auto_match_review_reason"] = automatic.get("match_review_reason", "")
        group["override_active"] = group["group_id"] in manual_matches
        group["override_expense_file"] = manual_matches.get(group["group_id"])
    apply_decisions_to_groups(current_groups, transaction_decisions)
    apply_allocations_to_groups(current_groups, transaction_allocations)

    state = {
        "version": RECONCILIATION_VERSION,
        "mode": selected_mode,
        "synced_at": datetime.now().isoformat(timespec="seconds"),
        "input_fingerprint": current_fingerprint,
        "requires_resync": False,
        "extracted_expenses": extracted_expenses,
        "expenses": [serialize_expense(expense) for expense in expenses],
        "estimated_cad_by_expense": estimated_cad_by_expense,
        "invoice_overrides": {
            filename: values
            for filename, values in invoice_overrides.items()
            if filename in expense_files
        },
        "manual_cad_overrides": {
            filename: values
            for filename, values in manual_cad_overrides.items()
            if filename in expense_files
        },
        "transaction_decisions": {
            group_id: values
            for group_id, values in transaction_decisions.items()
            if group_id in transaction_groups
        },
        "transaction_allocations": {
            group_id: values
            for group_id, values in transaction_allocations.items()
            if group_id in transaction_groups
        },
        "transactions": current_groups,
        "coverage_confirmation": None,
        "manual_matches": manual_matches,
        "warnings": list(normalization.warnings),
    }
    save_reconciliation_state(trip_dir, state)
    emit_progress(progress_callback, "complete", 1, 1, "Reconciliation ready for review")
    return reconciliation_view(trip_dir, state)


def estimate_expense_cad_amounts(
    expenses: list[Expense],
    transactions: list[NormalizedTransaction],
    fx_resolver: WeeklyCadFxResolver,
    warning_callback: WarningCallback | None = None,
) -> dict[str, float]:
    """Estimate each employee's share in CAD for matching suggestions only."""

    estimates: dict[str, float] = {}
    warned: set[tuple[str, str]] = set()
    for expense in expenses:
        if expense.amount is None or not expense.date or not expense.currency:
            continue
        people = max(1, int(expense.number_of_people or 1))
        share = abs(float(expense.amount)) / people
        currency = str(expense.currency).upper()
        if any(
            transaction.match_eligible
            and str(transaction.purchase_currency or "").upper() == currency
            and isinstance(transaction.purchase_amount, (int, float))
            and close_amount(share, abs(float(transaction.purchase_amount)))
            for transaction in transactions
        ):
            continue
        nearby_cad_candidates = [
            transaction
            for transaction in transactions
            if transaction.match_eligible
            and isinstance(transaction.cad_amount, (int, float))
            and (
                (delta := days_between(expense.date, transaction.transaction_date)) is not None
                and delta <= 7
            )
        ]
        if not nearby_cad_candidates:
            continue
        try:
            rate = fx_resolver.resolve(currency, expense.date)
        except FxRateUnavailable as exc:
            warning_key = (currency, expense.date)
            if warning_callback and warning_key not in warned:
                warning_callback(
                    f"FX-aware receipt matching unavailable for {currency} on {expense.date}: {exc}"
                )
                warned.add(warning_key)
            continue
        estimates[source_file_key(expense.source_file)] = round(share * rate.cad_per_unit, 2)
    return estimates


def set_manual_match(
    trip_dir: Path,
    group_id: str,
    expense_file: str | None = None,
    use_auto: bool = False,
) -> dict:
    state = load_reconciliation_state(trip_dir)
    if not state:
        raise ValueError("Run invoice and statement sync before editing mappings.")
    if not reconciliation_is_fresh(trip_dir, state):
        raise ValueError(
            "Receipts or statements changed after the last sync. Sync again before editing mappings."
        )
    transaction = next(
        (item for item in state.get("transactions", []) if item.get("group_id") == group_id), None
    )
    if not transaction:
        raise FileNotFoundError(
            "The statement transaction is no longer present in the reconciliation snapshot."
        )
    if transaction.get("ignored"):
        raise ValueError("Restore this ignored transaction before changing its invoice mapping.")
    if state.get("transaction_allocations", {}).get(group_id):
        raise ValueError(
            "Clear this transaction's split allocations before using the simple invoice mapping."
        )

    manual_matches = state.setdefault("manual_matches", {})
    if use_auto:
        manual_matches.pop(group_id, None)
        transaction["expense_file"] = transaction.get("auto_expense_file")
        transaction["match_status"] = transaction.get("auto_match_status", "unmatched")
        transaction["match_confidence"] = transaction.get(
            "auto_match_confidence", transaction.get("match_confidence", 0.0)
        )
        transaction["match_review_reason"] = transaction.get("auto_match_review_reason", "")
        transaction["override_active"] = False
        transaction["override_expense_file"] = None
    else:
        available_files = {expense.get("source_file") for expense in state.get("expenses", [])}
        if expense_file is not None and expense_file not in available_files:
            raise ValueError("Choose an invoice from the current reconciliation snapshot.")
        manual_matches[group_id] = expense_file
        transaction["expense_file"] = expense_file
        transaction["match_status"] = "manual" if expense_file else "unmatched"
        transaction["match_confidence"] = 1.0 if expense_file else 0.0
        transaction["match_review_reason"] = ""
        transaction["override_active"] = True
        transaction["override_expense_file"] = expense_file

    save_reconciliation_state(trip_dir, state)
    return reconciliation_view(trip_dir, state)


def load_manual_matches(trip_dir: Path) -> dict[str, str | None]:
    state = load_reconciliation_state(trip_dir)
    if state and not reconciliation_is_fresh(trip_dir, state):
        return {}
    return deserialize_manual_matches(state)


def load_transaction_decisions(trip_dir: Path) -> dict[str, dict]:
    state = load_reconciliation_state(trip_dir)
    if state and not reconciliation_is_fresh(trip_dir, state):
        return {}
    return deserialize_transaction_decisions(state)


def load_transaction_allocations(trip_dir: Path) -> dict[str, list[dict]]:
    state = load_reconciliation_state(trip_dir)
    if state and not reconciliation_is_fresh(trip_dir, state):
        return {}
    return deserialize_transaction_allocations(state)


def apply_transaction_decisions(
    transactions: list[NormalizedTransaction],
    decisions: dict[str, dict],
) -> None:
    """Apply reviewed exclusions while retaining every statement row for audit."""

    for transaction in transactions:
        decision = decisions.get(transaction.transaction_group_id)
        if not decision:
            continue
        action = decision.get("action")
        note = str(decision.get("note", "")).strip()
        timestamp = str(decision.get("updated_at", "")).strip()
        if action == "ignore":
            possible_duplicate = transaction.normalization_status == "possible_duplicate"
            transaction.match_eligible = False
            transaction.expense_id = ""
            transaction.suggested_expense_id = ""
            transaction.match_status = "ignored"
            transaction.normalization_status = "ignored"
            audit = (
                f"Ignored as duplicate: {note}"
                if possible_duplicate
                else f"Excluded from this trip: {note}"
            )
        else:
            transaction.normalization_status = "duplicate_confirmed"
            audit = "Confirmed as a legitimate repeated transaction."
        if timestamp:
            audit = f"{audit} Decision recorded {timestamp}."
        transaction.review_note = append_reconciliation_note(transaction.review_note, audit)


def set_transaction_decision(
    trip_dir: Path,
    group_id: str,
    action: str,
    note: str = "",
) -> dict:
    """Persist an auditable statement disposition.

    Possible duplicates support an explicit keep/ignore decision. Any normal
    match-eligible statement row may also be excluded from the trip with a
    reason, which is required for subscriptions, personal charges, and other
    statement activity that does not belong to the business trip.
    """

    state = load_reconciliation_state(trip_dir)
    if not state:
        raise ValueError("Run invoice and statement sync before reviewing transactions.")
    if not reconciliation_is_fresh(trip_dir, state):
        raise ValueError(
            "Receipts or statements changed after the last sync. Sync again before reviewing transactions."
        )
    transaction = next(
        (item for item in state.get("transactions", []) if item.get("group_id") == group_id), None
    )
    if not transaction:
        raise FileNotFoundError(
            "The statement transaction is no longer present in the reconciliation snapshot."
        )
    if state.get("transaction_allocations", {}).get(group_id):
        raise ValueError("Clear split allocations before changing the transaction disposition.")
    action = action.strip().lower()
    if action not in {"unresolved", "keep", "ignore"}:
        raise ValueError("Choose unresolved, keep, or ignore.")
    note = note.strip()
    if action == "ignore" and not note:
        raise ValueError("Explain why this transaction should be ignored.")

    decisions = state.setdefault("transaction_decisions", {})
    restore_group_before_decision(transaction)
    if action == "unresolved":
        decisions.pop(group_id, None)
        transaction["duplicate_resolution"] = (
            "unresolved" if transaction.get("possible_duplicate") else None
        )
        if transaction.get("possible_duplicate"):
            transaction["normalization_status"] = "possible_duplicate"
    elif action == "keep" and not transaction.get("possible_duplicate"):
        decisions.pop(group_id, None)
        transaction["duplicate_resolution"] = None
    else:
        decision = {
            "action": action,
            "note": note,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        decisions[group_id] = decision
        apply_decision_to_group(transaction, decision)
    state["coverage_confirmation"] = None
    save_reconciliation_state(trip_dir, state)
    return reconciliation_view(trip_dir, state)


def set_transaction_allocations(
    trip_dir: Path,
    group_id: str,
    allocations: list[dict],
) -> dict:
    state = load_reconciliation_state(trip_dir)
    if not state:
        raise ValueError("Run invoice and statement sync before allocating transactions.")
    if not reconciliation_is_fresh(trip_dir, state):
        raise ValueError(
            "Receipts or statements changed after the last sync. Sync again before allocating."
        )
    group = next(
        (item for item in state.get("transactions", []) if item.get("group_id") == group_id), None
    )
    if not group:
        raise FileNotFoundError(
            "The statement transaction is no longer present in the reconciliation snapshot."
        )
    if group.get("ignored"):
        raise ValueError("Restore this ignored transaction before adding allocations.")
    if group.get("cad_completeness") != "complete" or not isinstance(
        group.get("cad_amount"), (int, float)
    ):
        raise ValueError("Split allocation requires a complete exact CAD statement amount.")
    available_files = {expense.get("source_file") for expense in state.get("expenses", [])}
    normalized = normalize_allocations(allocations, available_files, float(group["cad_amount"]))
    stored = state.setdefault("transaction_allocations", {})
    restore_group_before_allocations(group)
    if normalized:
        stored[group_id] = normalized
        apply_allocations_to_group(group, normalized)
    else:
        stored.pop(group_id, None)
    state["coverage_confirmation"] = None
    save_reconciliation_state(trip_dir, state)
    return reconciliation_view(trip_dir, state)


def set_coverage_settings(trip_dir: Path, expected_accounts: list[str]) -> dict:
    cleaned = []
    for account in expected_accounts:
        value = " ".join(str(account).split()).strip()
        if value and value.casefold() not in {item.casefold() for item in cleaned}:
            cleaned.append(value)
    if len(cleaned) > 30:
        raise ValueError("Add no more than 30 expected cards or accounts.")
    config = load_trip_config(trip_dir)
    config["expected_accounts"] = cleaned
    save_trip_config(trip_dir, config)
    state = load_reconciliation_state(trip_dir)
    if state:
        state["coverage_confirmation"] = None
        save_reconciliation_state(trip_dir, state)
    return statement_coverage_view(trip_dir, state)


def confirm_statement_coverage(trip_dir: Path, note: str = "") -> dict:
    state = load_reconciliation_state(trip_dir)
    if not state or not reconciliation_is_fresh(trip_dir, state):
        raise ValueError("Sync current statements before confirming their coverage.")
    coverage = statement_coverage_view(trip_dir, state)
    note = note.strip()
    if coverage["gaps"] and not note:
        raise ValueError("Explain the unresolved statement coverage gaps before generating.")
    confirmation = {
        "confirmed_at": datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "gap_count": len(coverage["gaps"]),
        "gaps": list(coverage["gaps"]),
    }
    state["coverage_confirmation"] = confirmation
    save_reconciliation_state(trip_dir, state)
    return confirmation


def apply_decisions_to_groups(groups: list[dict], decisions: dict[str, dict]) -> None:
    for group in groups:
        decision = decisions.get(group["group_id"])
        if decision:
            apply_decision_to_group(group, decision)


def apply_decision_to_group(group: dict, decision: dict) -> None:
    group.setdefault(
        "pre_decision",
        {
            "expense_file": group.get("expense_file"),
            "match_status": group.get("match_status"),
            "match_confidence": group.get("match_confidence"),
            "match_eligible": group.get("match_eligible"),
            "normalization_status": group.get("normalization_status"),
        },
    )
    action = decision.get("action")
    group["duplicate_resolution"] = action
    group["decision_note"] = decision.get("note", "")
    group["decision_updated_at"] = decision.get("updated_at")
    if action == "ignore":
        group["ignored"] = True
        group["expense_file"] = None
        group["match_status"] = "ignored"
        group["match_confidence"] = 0.0
        group["normalization_status"] = "ignored"
    else:
        group["ignored"] = False
        group["normalization_status"] = "duplicate_confirmed"
        if (
            not group.get("expense_file")
            and group.get("suggested_expense_file")
            and float(group.get("match_confidence") or 0) >= 0.72
            and not group.get("match_review_reason")
        ):
            group["expense_file"] = group["suggested_expense_file"]
            group["match_status"] = "auto"


def restore_group_before_decision(group: dict) -> None:
    previous = group.get("pre_decision", {})
    for field in (
        "expense_file",
        "match_status",
        "match_confidence",
        "match_eligible",
        "normalization_status",
    ):
        if field in previous:
            group[field] = previous[field]
    group["ignored"] = False
    group["decision_note"] = ""
    group["decision_updated_at"] = None


def apply_reconciliation_overrides(trip_dir: Path, expenses: list[Expense]) -> None:
    """Apply persisted invoice and CAD decisions to freshly extracted expenses."""

    apply_invoice_overrides(expenses, load_invoice_overrides(trip_dir))
    apply_manual_cad_overrides(expenses, load_manual_cad_overrides(trip_dir))
    assign_simple_expense_ids(expenses)


def set_invoice_review(
    trip_dir: Path,
    source_file: str,
    fields: dict | None = None,
    restore_extracted: bool = False,
    update_manual_cad: bool = False,
    manual_cad: dict | None = None,
) -> dict:
    """Support older API callers that stored review fields with reconciliation.

    The browser now writes these decisions through the canonical receipt review
    model in ``line_items.py``. This compatibility boundary remains so existing
    callers and saved reconciliation snapshots can still be reopened safely.
    """

    state = load_reconciliation_state(trip_dir)
    if not state:
        raise ValueError("Run invoice and statement sync before reviewing invoices.")
    if state.get("input_fingerprint") != reconciliation_input_fingerprint(trip_dir):
        raise ValueError(
            "Receipts or statements changed after the last sync. Sync again before reviewing invoices."
        )

    extracted = state.get("extracted_expenses") or state.get("expenses") or []
    extracted_invoice = next(
        (item for item in extracted if item.get("source_file") == source_file), None
    )
    if not extracted_invoice:
        raise FileNotFoundError("The invoice is no longer present in the reconciliation snapshot.")

    invoice_overrides = state.setdefault("invoice_overrides", {})
    previous_fields = dict(invoice_overrides.get(source_file, {}))
    if restore_extracted:
        invoice_overrides.pop(source_file, None)
    elif fields is not None:
        normalized = validate_invoice_fields(fields, extracted_invoice)
        invoice_overrides[source_file] = {
            field: value
            for field, value in normalized.items()
            if invoice_values_differ(value, extracted_invoice.get(field))
        }
        if not invoice_overrides[source_file]:
            invoice_overrides.pop(source_file, None)

    if previous_fields != invoice_overrides.get(source_file, {}):
        state["requires_resync"] = True

    if update_manual_cad:
        cad_overrides = state.setdefault("manual_cad_overrides", {})
        if manual_cad is None:
            cad_overrides.pop(source_file, None)
        else:
            amount, note = validate_manual_cad(manual_cad)
            cad_overrides[source_file] = {
                "amount": amount,
                "note": note,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }

    state["expenses"] = apply_overrides_to_snapshots(extracted, state.get("invoice_overrides", {}))
    save_reconciliation_state(trip_dir, state)
    return reconciliation_view(trip_dir, state)


def ensure_reconciliation_ready(trip_dir: Path) -> None:
    """Require a current app mapping whenever the trip has statements."""

    statements = reconciliation_statement_files(trip_dir, trip_mode(trip_dir))
    if not statements:
        return
    state = load_reconciliation_state(trip_dir)
    if not state:
        raise ValueError(
            "Run Sync and auto-match before generating so the statement mappings belong to the current files."
        )
    if not reconciliation_is_fresh(trip_dir, state):
        raise ValueError(
            "Receipts or statements changed after the last sync. Sync again before generating the workbook."
        )
    incomplete_allocations = [
        group
        for group in state.get("transactions", [])
        if group.get("allocations") and group.get("allocation_status") != "balanced"
    ]
    if incomplete_allocations:
        raise ValueError(
            "Finish split allocations before generating; every allocated transaction must balance to its CAD statement amount."
        )


def aggregate_transaction_groups(
    expenses: list[Expense], transactions: list[NormalizedTransaction]
) -> list[dict]:
    expenses_by_id = {expense.expense_id: expense for expense in expenses}
    grouped: dict[str, list[NormalizedTransaction]] = defaultdict(list)
    for transaction in transactions:
        grouped[transaction.transaction_group_id].append(transaction)

    result: list[dict] = []
    for group_id, legs in grouped.items():
        eligible = [leg for leg in legs if leg.match_eligible]
        representative = eligible[0] if eligible else legs[0]
        purchase_currency = common_value([leg.purchase_currency for leg in eligible])
        purchase_amount = (
            sum_values([leg.purchase_amount for leg in eligible]) if purchase_currency else None
        )
        completeness = representative.cad_completeness
        cad_amount = (
            sum_values([leg.cad_amount for leg in eligible]) if completeness == "complete" else None
        )
        rate_values = {
            round(float(leg.cad_conversion_rate), 10)
            for leg in eligible
            if leg.cad_conversion_rate is not None
        }
        if len(rate_values) == 1:
            cad_conversion_rate = next(iter(rate_values))
        elif cad_amount is not None and purchase_amount not in (None, 0):
            cad_conversion_rate = round(abs(cad_amount) / abs(purchase_amount), 10)
        else:
            cad_conversion_rate = None
        conversion_methods = {
            leg.cad_conversion_method for leg in eligible if leg.cad_conversion_method
        }
        conversion_routes = {
            leg.cad_conversion_route for leg in eligible if leg.cad_conversion_route
        }
        conversion_sources = list(
            dict.fromkeys(
                leg.cad_conversion_source for leg in eligible if leg.cad_conversion_source
            )
        )
        conversion_source_urls = list(
            dict.fromkeys(url for leg in eligible for url in leg.cad_conversion_source_urls if url)
        )
        expense_id = common_value([leg.expense_id for leg in eligible])
        suggested_id = common_value([leg.suggested_expense_id for leg in eligible])
        expense = expenses_by_id.get(expense_id or "")
        suggested = expenses_by_id.get(suggested_id or "")
        statuses = {leg.match_status for leg in eligible}
        if "manual" in statuses:
            status = "manual"
        elif "auto" in statuses:
            status = "auto"
        elif "suggested" in statuses:
            status = "suggested"
        else:
            status = "unmatched" if eligible else "audit"
        normalization_statuses = {leg.normalization_status for leg in legs}
        possible_duplicate = "possible_duplicate" in normalization_statuses
        if possible_duplicate:
            normalization_status = "possible_duplicate"
        elif "review" in normalization_statuses:
            normalization_status = "review"
        else:
            normalization_status = next(iter(normalization_statuses), "ok")
        notes = list(dict.fromkeys(leg.review_note for leg in legs if leg.review_note))
        result.append(
            {
                "group_id": group_id,
                "transaction_date": representative.transaction_date,
                "provider": representative.provider,
                "account_label": representative.account_label,
                "description": representative.description,
                "transaction_type": representative.transaction_type,
                "purchase_amount": purchase_amount,
                "purchase_currency": purchase_currency,
                "cad_amount": cad_amount,
                "cad_completeness": completeness,
                "cad_conversion_rate": cad_conversion_rate,
                "cad_conversion_week_start": common_value(
                    [leg.cad_conversion_week_start for leg in eligible]
                ),
                "cad_conversion_week_end": common_value(
                    [leg.cad_conversion_week_end for leg in eligible]
                ),
                "cad_conversion_method": (
                    next(iter(conversion_methods))
                    if len(conversion_methods) == 1
                    else "mixed"
                    if conversion_methods
                    else ""
                ),
                "cad_conversion_route": (
                    next(iter(conversion_routes))
                    if len(conversion_routes) == 1
                    else "mixed"
                    if conversion_routes
                    else ""
                ),
                "cad_conversion_source": "; ".join(conversion_sources),
                "cad_conversion_source_urls": conversion_source_urls,
                "match_eligible": bool(eligible),
                "expense_file": source_file_key(expense.source_file) if expense else None,
                "suggested_expense_file": source_file_key(suggested.source_file)
                if suggested
                else None,
                "match_status": status,
                "match_confidence": max((leg.match_confidence for leg in legs), default=0.0),
                "auto_match_confidence": max((leg.match_confidence for leg in legs), default=0.0),
                "match_review_reason": common_value([leg.match_review_reason for leg in eligible])
                or "",
                "normalization_status": normalization_status,
                "review_note": " ".join(notes),
                "possible_duplicate": possible_duplicate,
                "duplicate_resolution": "unresolved" if possible_duplicate else None,
                "ignored": False,
                "allocations": [],
                "allocation_total": 0.0,
                "allocation_balance": cad_amount,
                "allocation_status": "none",
                "source_files": sorted({leg.source_file.name for leg in legs}),
                "source_rows": [
                    {"file": leg.source_file.name, "row": leg.source_row} for leg in legs
                ],
                "funding_legs": [
                    {
                        "funding_leg_id": leg.funding_leg_id,
                        "source_file": leg.source_file.name,
                        "source_row": leg.source_row,
                        "settlement_amount": leg.settlement_amount,
                        "settlement_currency": leg.settlement_currency,
                        "cad_amount": leg.cad_amount,
                        "cad_conversion_rate": leg.cad_conversion_rate,
                        "cad_conversion_week_start": leg.cad_conversion_week_start,
                        "cad_conversion_week_end": leg.cad_conversion_week_end,
                        "cad_conversion_method": leg.cad_conversion_method,
                        "cad_conversion_route": leg.cad_conversion_route,
                        "cad_conversion_source": leg.cad_conversion_source,
                        "cad_conversion_source_urls": leg.cad_conversion_source_urls,
                        "normalization_status": leg.normalization_status,
                        "review_note": leg.review_note,
                    }
                    for leg in legs
                ],
                "funding_leg_count": len(legs),
            }
        )
    return sorted(
        result,
        key=lambda item: (
            item["transaction_date"] or "9999-99-99",
            item["description"],
            item["group_id"],
        ),
    )


def serialize_expense(expense: Expense) -> dict:
    return {
        "source_file": source_file_key(expense.source_file),
        "expense_id": expense.expense_id,
        "date": expense.date,
        "vendor": expense.supplier_name,
        "description": expense.description,
        "expense_type": expense.expense_type,
        "amount": expense.amount,
        "currency": expense.currency,
        "country": expense.country,
        "province": expense.province,
        "gst_hst": expense.gst_hst,
        "qst": expense.qst,
        "gst_hst_number": expense.gst_hst_number,
        "qst_number": expense.qst_number,
        "business_purpose": expense.business_purpose,
        "attendees_client": expense.attendees_client,
        "confidence": expense.confidence,
        "tax_documentation_status": expense.tax_documentation_status,
        "review_note": expense.review_note,
        "included": expense.included,
        "number_of_people": expense.number_of_people,
        "manual_cad_override": expense.manual_cad_override,
        "manual_cad_note": expense.manual_cad_note,
    }


def reconciliation_statement_files(trip_dir: Path, mode: str) -> list[Path]:
    if mode == "arvine":
        return list_statement_files(trip_statements_dir(trip_dir))
    supported = {".csv", ".xls", ".xlsx", ".pdf"}
    return sorted(
        path
        for path in trip_statements_dir(trip_dir).iterdir()
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in supported
    )


def normalize_ivado_statement_files(paths: list[Path]) -> NormalizationResult:
    """Adapt IVADO's permissive parser to the shared reconciliation contract."""

    result = NormalizationResult()
    for path in paths:
        parsed = parse_statement_file(path)
        report = StatementFileReport(
            source_file=path,
            provider="IVADO imported statement",
            rows_read=len(parsed),
            rows_normalized=len(parsed),
        )
        if not parsed:
            warning = f"{path.name}: no statement transactions were recognized; verify the file in the mapping review."
            report.warnings.append(warning)
            result.warnings.append(warning)
        result.files.append(report)
        for source_row, transaction in enumerate(parsed, start=2):
            signature = "|".join(
                [
                    path.name,
                    str(source_row),
                    str(transaction.date or ""),
                    transaction.description,
                    str(transaction.amount_cad),
                    str(transaction.foreign_amount),
                    str(transaction.foreign_currency or ""),
                ]
            )
            group_id = f"IVADO-{hashlib.sha256(signature.encode('utf-8')).hexdigest()[:20]}"
            purchase_amount = (
                transaction.foreign_amount
                if transaction.foreign_amount is not None
                else transaction.amount_cad
            )
            purchase_currency = transaction.foreign_currency or "CAD"
            result.transactions.append(
                NormalizedTransaction(
                    source_file=path,
                    source_row=source_row,
                    provider="IVADO",
                    transaction_group_id=group_id,
                    funding_leg_id=f"{group_id}:1",
                    transaction_date=transaction.date,
                    posted_date=transaction.date,
                    account_label=path.stem,
                    description=transaction.description,
                    transaction_type="refund"
                    if isinstance(transaction.amount_cad, (int, float))
                    and transaction.amount_cad < 0
                    else "purchase",
                    direction="in"
                    if isinstance(transaction.amount_cad, (int, float))
                    and transaction.amount_cad < 0
                    else "out",
                    match_eligible=transaction.amount_cad is not None,
                    purchase_amount=purchase_amount,
                    purchase_currency=purchase_currency,
                    settlement_amount=transaction.amount_cad,
                    settlement_currency="CAD",
                    cad_amount=transaction.amount_cad,
                    cad_completeness="complete"
                    if transaction.amount_cad is not None
                    else "incomplete",
                    normalization_status="ok",
                )
            )
    return result


def ivado_statement_transactions_from_reconciliation(
    trip_dir: Path,
    expenses: list[Expense],
) -> list[StatementTransaction]:
    """Build IVADO workbook rows from the exact mappings reviewed in the app."""

    state = load_reconciliation_state(trip_dir)
    if not state or not reconciliation_is_fresh(trip_dir, state):
        return []
    expense_ids = {source_file_key(expense.source_file): expense.expense_id for expense in expenses}
    transactions = []
    for group in state.get("transactions", []):
        source_files = group.get("source_files") or []
        source_name = str(source_files[0]) if source_files else "statement"
        source_file = trip_statements_dir(trip_dir) / source_name
        expense_file = group.get("expense_file") if not group.get("ignored") else None
        transactions.append(
            StatementTransaction(
                source_file=source_file,
                date=group.get("transaction_date"),
                description=str(group.get("description") or ""),
                amount_cad=group.get("cad_amount"),
                foreign_amount=group.get("purchase_amount")
                if group.get("purchase_currency") not in (None, "", "CAD")
                else None,
                foreign_currency=group.get("purchase_currency")
                if group.get("purchase_currency") not in (None, "", "CAD")
                else None,
                expense_id=expense_ids.get(expense_file, ""),
                suggested_expense_id=expense_ids.get(group.get("suggested_expense_file"), ""),
                match_confidence=float(group.get("match_confidence") or 0.0),
            )
        )
    return transactions


def append_reconciliation_note(existing: str, note: str) -> str:
    return f"{existing.rstrip()} {note}".strip() if existing else note


def emit_progress(
    callback: ProgressCallback | None,
    stage: str,
    current: int,
    total: int,
    message: str,
) -> None:
    if callback:
        callback(GenerationProgress(stage=stage, current=current, total=total, message=message))
