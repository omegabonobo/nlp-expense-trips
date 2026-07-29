from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from nlp_expenses.generator import (
    ProgressCallback,
    WarningCallback,
    assign_simple_expense_ids,
    extract_trip_expenses,
)
from nlp_expenses.matching import apply_manual_matches, common_value, match_normalized_transactions, sum_values
from nlp_expenses.extraction.statements import parse_statement_file
from nlp_expenses.line_items import apply_line_item_review, save_line_item_review
from nlp_expenses.models import (
    Expense,
    GenerationProgress,
    NormalizationResult,
    NormalizedTransaction,
    StatementFileReport,
    StatementTransaction,
)
from nlp_expenses.statement_normalizer import (
    STATEMENT_SETTINGS_FILE,
    StatementNormalizationError,
    list_statement_files,
    normalize_statement_files,
    preflight_statement_files,
)
from nlp_expenses.trip_metadata import (
    apply_trip_metadata_defaults,
    trip_metadata,
    trip_policy_warnings,
)
from nlp_expenses.trips import (
    list_receipt_files,
    load_trip_config,
    relative_source_name,
    save_trip_config,
    source_file_key,
    trip_mode,
    trip_receipts_dir,
    trip_statements_dir,
)


RECONCILIATION_FILE = ".nlp-expenses-reconciliation.json"
RECONCILIATION_VERSION = 1
INVOICE_FIELD_MAP = {
    "date": "date",
    "vendor": "supplier_name",
    "description": "description",
    "expense_type": "expense_type",
    "amount": "amount",
    "currency": "currency",
    "country": "country",
    "province": "province",
    "gst_hst": "gst_hst",
    "qst": "qst",
    "gst_hst_number": "gst_hst_number",
    "qst_number": "qst_number",
    "business_purpose": "business_purpose",
    "attendees_client": "attendees_client",
}
NUMERIC_INVOICE_FIELDS = {"amount", "gst_hst", "qst"}
ALLOCATION_TYPES = {"purchase", "refund", "fee", "personal", "ignored"}
REIMBURSABLE_ALLOCATION_TYPES = {"purchase", "refund", "fee"}


class InvoiceValidationError(ValueError):
    def __init__(self, fields: dict[str, str]):
        self.fields = fields
        super().__init__("Correct the highlighted invoice fields.")


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
    emit_progress(progress_callback, "statements", 0, len(statements), "Validating card and bank statements")
    if selected_mode == "arvine":
        reports = preflight_statement_files(statements)
        errors = [error for report in reports for error in report.errors]
        if errors:
            raise StatementNormalizationError("\n".join(errors))
        normalization = normalize_statement_files(statements)
        if normalization.errors:
            raise StatementNormalizationError("\n".join(normalization.errors))
    else:
        normalization = normalize_ivado_statement_files(statements)
    if warning_callback:
        for message in normalization.warnings:
            warning_callback(message)
    emit_progress(progress_callback, "statements", len(statements), len(statements), "Statements normalized")

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
    emit_progress(progress_callback, "matching", 0, 1, "Matching invoices to statement transactions")
    match_normalized_transactions(expenses, normalization.transactions)

    expense_files = {source_file_key(expense.source_file) for expense in expenses}
    transaction_groups = {transaction.transaction_group_id for transaction in normalization.transactions}
    preserved_manual_matches = (
        deserialize_manual_matches(previous_state)
        if previous_state and previous_state.get("input_fingerprint") == current_fingerprint
        else {}
    )
    manual_matches = {
        group_id: receipt_file
        for group_id, receipt_file in preserved_manual_matches.items()
        if group_id in transaction_groups and (receipt_file is None or receipt_file in expense_files)
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
        "invoice_overrides": {
            filename: values for filename, values in invoice_overrides.items() if filename in expense_files
        },
        "manual_cad_overrides": {
            filename: values for filename, values in manual_cad_overrides.items() if filename in expense_files
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
        raise ValueError("Receipts or statements changed after the last sync. Sync again before editing mappings.")
    transaction = next((item for item in state.get("transactions", []) if item.get("group_id") == group_id), None)
    if not transaction:
        raise FileNotFoundError("The statement transaction is no longer present in the reconciliation snapshot.")
    if transaction.get("ignored"):
        raise ValueError("Restore this ignored transaction before changing its invoice mapping.")
    if state.get("transaction_allocations", {}).get(group_id):
        raise ValueError("Clear this transaction's split allocations before using the simple invoice mapping.")

    manual_matches = state.setdefault("manual_matches", {})
    if use_auto:
        manual_matches.pop(group_id, None)
        transaction["expense_file"] = transaction.get("auto_expense_file")
        transaction["match_status"] = transaction.get("auto_match_status", "unmatched")
        transaction["match_confidence"] = transaction.get("auto_match_confidence", transaction.get("match_confidence", 0.0))
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
        transaction["override_active"] = True
        transaction["override_expense_file"] = expense_file

    save_reconciliation_state(trip_dir, state)
    return reconciliation_view(trip_dir, state)


def load_manual_matches(trip_dir: Path) -> dict[str, str | None]:
    state = load_reconciliation_state(trip_dir)
    if state and not reconciliation_is_fresh(trip_dir, state):
        return {}
    return deserialize_manual_matches(state)


def deserialize_manual_matches(state: dict | None) -> dict[str, str | None]:
    raw = state.get("manual_matches", {}) if state else {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(group_id): (str(receipt_file) if receipt_file is not None else None)
        for group_id, receipt_file in raw.items()
        if isinstance(group_id, str) and (isinstance(receipt_file, str) or receipt_file is None)
    }


def load_invoice_overrides(trip_dir: Path) -> dict[str, dict]:
    state = load_reconciliation_state(trip_dir)
    raw = state.get("invoice_overrides", {}) if state else {}
    if not isinstance(raw, dict):
        return {}
    return {
        filename: dict(values)
        for filename, values in raw.items()
        if isinstance(filename, str) and isinstance(values, dict)
    }


def load_manual_cad_overrides(trip_dir: Path) -> dict[str, dict]:
    state = load_reconciliation_state(trip_dir)
    raw = state.get("manual_cad_overrides", {}) if state else {}
    if not isinstance(raw, dict):
        return {}
    return {
        filename: dict(values)
        for filename, values in raw.items()
        if isinstance(filename, str) and isinstance(values, dict)
    }


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


def deserialize_transaction_allocations(state: dict | None) -> dict[str, list[dict]]:
    raw = state.get("transaction_allocations", {}) if state else {}
    if not isinstance(raw, dict):
        return {}
    return {
        group_id: [dict(allocation) for allocation in allocations if isinstance(allocation, dict)]
        for group_id, allocations in raw.items()
        if isinstance(group_id, str) and isinstance(allocations, list)
    }


def deserialize_transaction_decisions(state: dict | None) -> dict[str, dict]:
    raw = state.get("transaction_decisions", {}) if state else {}
    if not isinstance(raw, dict):
        return {}
    return {
        group_id: dict(values)
        for group_id, values in raw.items()
        if isinstance(group_id, str)
        and isinstance(values, dict)
        and values.get("action") in {"keep", "ignore"}
    }


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
        raise ValueError("Receipts or statements changed after the last sync. Sync again before reviewing transactions.")
    transaction = next((item for item in state.get("transactions", []) if item.get("group_id") == group_id), None)
    if not transaction:
        raise FileNotFoundError("The statement transaction is no longer present in the reconciliation snapshot.")
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
        raise ValueError("Receipts or statements changed after the last sync. Sync again before allocating.")
    group = next((item for item in state.get("transactions", []) if item.get("group_id") == group_id), None)
    if not group:
        raise FileNotFoundError("The statement transaction is no longer present in the reconciliation snapshot.")
    if group.get("ignored"):
        raise ValueError("Restore this ignored transaction before adding allocations.")
    if group.get("cad_completeness") != "complete" or not isinstance(group.get("cad_amount"), (int, float)):
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


def normalize_allocations(
    allocations: list[dict],
    available_files: set[str | None],
    target_cad: float,
) -> list[dict]:
    if not isinstance(allocations, list):
        raise ValueError("Allocations must be submitted as a list.")
    normalized = []
    for index, allocation in enumerate(allocations, start=1):
        if not isinstance(allocation, dict):
            raise ValueError(f"Allocation {index} must be an object.")
        allocation_type = str(allocation.get("type", "")).strip().lower()
        if allocation_type not in ALLOCATION_TYPES:
            raise ValueError(f"Allocation {index} has an invalid type.")
        invoice_file = str(allocation.get("invoice_file") or "").strip() or None
        category = str(allocation.get("category") or "").strip()
        note = str(allocation.get("note") or "").strip()
        if invoice_file and invoice_file not in available_files:
            raise ValueError(f"Allocation {index} references an invoice that is no longer present.")
        if allocation_type in {"purchase", "refund"} and not invoice_file:
            raise ValueError(f"Allocation {index} must reference an invoice.")
        if allocation_type in {"personal", "ignored"} and not category:
            category = "Personal / non-reimbursable" if allocation_type == "personal" else "Ignored"
        if allocation_type in {"personal", "ignored"} and not note:
            raise ValueError(f"Allocation {index} needs an audit note.")
        try:
            cad_amount = round(float(allocation.get("cad_amount")), 2)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Allocation {index} needs a valid CAD amount.") from exc
        if cad_amount == 0:
            raise ValueError(f"Allocation {index} CAD amount cannot be zero.")
        if allocation_type == "refund" and cad_amount > 0:
            raise ValueError(f"Allocation {index} refund amount must be negative.")
        if allocation_type != "refund" and cad_amount < 0:
            raise ValueError(f"Allocation {index} amount must be positive.")
        original = allocation.get("original_amount")
        if original in ("", None):
            original_amount = None
        else:
            try:
                original_amount = round(float(original), 2)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Allocation {index} original amount is invalid.") from exc
        percentage = round(abs(cad_amount / target_cad) * 100, 4) if target_cad else 0.0
        normalized.append(
            {
                "allocation_id": f"A{index:03d}",
                "type": allocation_type,
                "invoice_file": invoice_file,
                "category": category,
                "original_amount": original_amount,
                "cad_amount": cad_amount,
                "percentage": percentage,
                "note": note,
            }
        )
    return normalized


def allocation_totals(allocations: list[dict], target_cad: float) -> dict:
    total = round(sum(float(item.get("cad_amount") or 0) for item in allocations), 2)
    balance = round(target_cad - total, 2)
    if not allocations:
        status = "none"
    elif abs(balance) <= 0.01:
        status = "balanced"
        balance = 0.0
    elif target_cad and (total * target_cad < 0 or abs(total) > abs(target_cad)):
        status = "overallocated"
    else:
        status = "unallocated"
    return {"allocation_total": total, "allocation_balance": balance, "allocation_status": status}


def apply_allocations_to_groups(groups: list[dict], allocations_by_group: dict[str, list[dict]]) -> None:
    for group in groups:
        allocations = allocations_by_group.get(group["group_id"], [])
        if allocations:
            apply_allocations_to_group(group, allocations)


def apply_allocations_to_group(group: dict, allocations: list[dict]) -> None:
    group.setdefault(
        "pre_allocation",
        {
            "expense_file": group.get("expense_file"),
            "match_status": group.get("match_status"),
            "match_confidence": group.get("match_confidence"),
        },
    )
    group["allocations"] = [dict(allocation) for allocation in allocations]
    group.update(allocation_totals(allocations, float(group.get("cad_amount") or 0)))
    group["expense_file"] = None
    group["match_status"] = "split"
    group["match_confidence"] = 1.0 if group["allocation_status"] == "balanced" else 0.0


def restore_group_before_allocations(group: dict) -> None:
    previous = group.get("pre_allocation", {})
    for field in ("expense_file", "match_status", "match_confidence"):
        if field in previous:
            group[field] = previous[field]
    group["allocations"] = []
    group["allocation_total"] = 0.0
    group["allocation_balance"] = group.get("cad_amount")
    group["allocation_status"] = "none"


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


def statement_coverage_view(trip_dir: Path, state: dict | None = None) -> dict:
    state = state if state is not None else load_reconciliation_state(trip_dir)
    groups = state.get("transactions", []) if state else []
    metadata = trip_metadata(trip_dir)
    expected = metadata["expected_accounts"]
    trip_start = date.fromisoformat(metadata["start_date"]) if metadata["start_date"] else None
    trip_end = date.fromisoformat(metadata["end_date"]) if metadata["end_date"] else None
    buffer_days = metadata["policy"]["statement_coverage_buffer_days"]
    coverage_start = trip_start - timedelta(days=buffer_days) if trip_start else None
    coverage_end = trip_end + timedelta(days=buffer_days) if trip_end else None
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for group in groups:
        provider = str(group.get("provider") or "unknown")
        account = str(group.get("account_label") or "Unlabelled account")
        grouped[(provider, account)].append(group)

    accounts = []
    present_labels: set[str] = set()
    for (provider, account), account_groups in sorted(grouped.items()):
        dates = sorted(str(group["transaction_date"]) for group in account_groups if group.get("transaction_date"))
        files = sorted(
            {
                str(source.get("file"))
                for group in account_groups
                for source in group.get("source_rows", [])
                if source.get("file")
            }
        )
        file_ranges: dict[str, list[str]] = defaultdict(list)
        for group in account_groups:
            if not group.get("transaction_date"):
                continue
            for source in group.get("source_rows", []):
                if source.get("file"):
                    file_ranges[str(source["file"])].append(str(group["transaction_date"]))
        normalized_ranges = [
            (filename, min(values), max(values))
            for filename, values in file_ranges.items()
            if values
        ]
        overlaps = any(
            left_start <= right_end and right_start <= left_end
            for index, (_left_file, left_start, left_end) in enumerate(normalized_ranges)
            for _right_file, right_start, right_end in normalized_ranges[index + 1 :]
        )
        unresolved_duplicates = sum(
            1
            for group in account_groups
            if group.get("possible_duplicate") and group.get("duplicate_resolution") == "unresolved"
        )
        label = f"{provider.upper()} {account}".strip()
        present_labels.update({label.casefold(), account.casefold(), provider.casefold()})
        accounts.append(
            {
                "provider": provider,
                "account_label": account,
                "label": label,
                "source_files": files,
                "earliest_date": dates[0] if dates else None,
                "latest_date": dates[-1] if dates else None,
                "transaction_count": len(account_groups),
                "match_eligible_count": sum(
                    1 for group in account_groups if group.get("match_eligible") and not group.get("ignored")
                ),
                "unresolved_duplicate_count": unresolved_duplicates,
                "overlapping_files": overlaps,
            }
        )

    gaps = [
        {
            "code": "missing_expected_account",
            "message": f"Expected card/account was not found: {expected_account}.",
        }
        for expected_account in expected
        if expected_account.casefold() not in present_labels
    ]
    for account in accounts:
        if account["overlapping_files"] and account["unresolved_duplicate_count"]:
            gaps.append(
                {
                    "code": "overlapping_exports",
                    "message": (
                        f"{account['label']} has overlapping exports and "
                        f"{account['unresolved_duplicate_count']} unresolved possible duplicate(s)."
                    ),
                }
            )
        if coverage_start and coverage_end:
            earliest = date.fromisoformat(account["earliest_date"]) if account["earliest_date"] else None
            latest = date.fromisoformat(account["latest_date"]) if account["latest_date"] else None
            if not earliest or not latest:
                gaps.append(
                    {
                        "code": "missing_account_dates",
                        "message": f"{account['label']} does not provide usable dates for trip coverage.",
                    }
                )
            elif latest < coverage_start or earliest > coverage_end:
                gaps.append(
                    {
                        "code": "no_trip_overlap",
                        "message": (
                            f"{account['label']} transaction dates do not overlap the expected "
                            f"{coverage_start.isoformat()} to {coverage_end.isoformat()} trip window."
                        ),
                    }
                )
            else:
                if earliest > coverage_start:
                    gaps.append(
                        {
                            "code": "coverage_starts_late",
                            "message": (
                                f"{account['label']} starts on {earliest.isoformat()}, after the expected "
                                f"coverage start {coverage_start.isoformat()}."
                            ),
                        }
                    )
                if latest < coverage_end:
                    gaps.append(
                        {
                            "code": "coverage_ends_early",
                            "message": (
                                f"{account['label']} ends on {latest.isoformat()}, before the expected "
                                f"coverage end {coverage_end.isoformat()}."
                            ),
                        }
                    )
    return {
        "accounts": accounts,
        "expected_accounts": expected,
        "trip_start": metadata["start_date"] or None,
        "trip_end": metadata["end_date"] or None,
        "buffer_days": buffer_days,
        "expected_start": coverage_start.isoformat() if coverage_start else None,
        "expected_end": coverage_end.isoformat() if coverage_end else None,
        "gaps": gaps,
        "confirmation": state.get("coverage_confirmation") if state else None,
    }


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


def apply_invoice_overrides(expenses: list[Expense], overrides: dict[str, dict]) -> None:
    for expense in expenses:
        values = overrides.get(source_file_key(expense.source_file), {})
        for field, attribute in INVOICE_FIELD_MAP.items():
            if field in values:
                setattr(expense, attribute, values[field])


def apply_manual_cad_overrides(expenses: list[Expense], overrides: dict[str, dict]) -> None:
    for expense in expenses:
        values = overrides.get(source_file_key(expense.source_file), {})
        amount = values.get("amount")
        if isinstance(amount, (int, float)):
            expense.manual_cad_override = float(amount)
            expense.manual_cad_note = str(values.get("note", ""))


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
        raise ValueError("Receipts or statements changed after the last sync. Sync again before reviewing invoices.")

    extracted = state.get("extracted_expenses") or state.get("expenses") or []
    extracted_invoice = next((item for item in extracted if item.get("source_file") == source_file), None)
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


def validate_invoice_fields(fields: dict, extracted: dict) -> dict:
    errors: dict[str, str] = {}
    normalized = {field: extracted.get(field) for field in INVOICE_FIELD_MAP}
    for field, value in fields.items():
        if field not in INVOICE_FIELD_MAP:
            errors[field] = "This field cannot be edited."
            continue
        if field in NUMERIC_INVOICE_FIELDS:
            if value in ("", None):
                normalized[field] = None
            else:
                try:
                    normalized[field] = round(float(value), 2)
                except (TypeError, ValueError):
                    errors[field] = "Enter a valid number."
        else:
            normalized[field] = str(value or "").strip()

    invoice_date = normalized.get("date")
    if invoice_date:
        try:
            date.fromisoformat(str(invoice_date))
        except ValueError:
            errors["date"] = "Use a valid date in YYYY-MM-DD format."
    currency = str(normalized.get("currency") or "").upper()
    normalized["currency"] = currency
    if currency and (len(currency) != 3 or not currency.isalpha()):
        errors["currency"] = "Use a three-letter currency code such as CAD or USD."
    amount = normalized.get("amount")
    if amount is not None and amount < 0:
        errors["amount"] = "A purchase total cannot be negative."
    for field in ("gst_hst", "qst"):
        tax = normalized.get(field)
        if tax is not None and tax < 0:
            errors[field] = "Tax cannot be negative."
        elif tax is not None and amount is not None and tax > amount:
            errors[field] = "Tax cannot be greater than the invoice total."
    taxes = sum(value or 0 for value in (normalized.get("gst_hst"), normalized.get("qst")))
    if amount is not None and amount >= 0 and taxes > amount:
        errors["gst_hst"] = "Combined tax cannot be greater than the invoice total."
        errors["qst"] = "Combined tax cannot be greater than the invoice total."
    if errors:
        raise InvoiceValidationError(errors)
    return normalized


def invoice_values_differ(reviewed, extracted) -> bool:
    if reviewed in (None, "") and extracted in (None, ""):
        return False
    return reviewed != extracted


def validate_manual_cad(values: dict) -> tuple[float, str]:
    errors: dict[str, str] = {}
    try:
        amount = round(float(values.get("amount")), 2)
    except (TypeError, ValueError):
        amount = 0.0
        errors["manual_cad_amount"] = "Enter a valid CAD amount."
    note = str(values.get("note", "")).strip()
    if amount <= 0:
        errors["manual_cad_amount"] = "The manual CAD amount must be greater than zero."
    if not note:
        errors["manual_cad_note"] = "Explain the source of the manual CAD amount."
    if errors:
        raise InvoiceValidationError(errors)
    return amount, note


def apply_overrides_to_snapshots(extracted: list[dict], overrides: dict[str, dict]) -> list[dict]:
    result = []
    for item in extracted:
        applied = dict(item)
        applied.update(overrides.get(str(item.get("source_file")), {}))
        result.append(applied)
    for index, item in enumerate(result, start=1):
        date_value = str(item.get("date") or "").replace("-", "") or "yyyymmdd"
        item["expense_id"] = f"{date_value}_#{index}"
    return result


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
                    if not isinstance(original_amount, (int, float)) and len(reimbursable_allocations) == 1:
                        original_amount = transaction.get("purchase_amount")
                    purchase_currency = str(transaction.get("purchase_currency") or "").upper()
                    if isinstance(original_amount, (int, float)) and original_amount and purchase_currency:
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
                if isinstance(purchase_amount, (int, float)) and purchase_amount and purchase_currency:
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
            (
                transaction.get("allocations")
                and transaction.get("allocation_status") != "balanced"
            )
            or (
                not transaction.get("allocations")
                and not transaction.get("expense_file")
            )
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
                if expense.get("cad_source") in {
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
                if expense.get("cad_source") in {
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

    policy_warnings = trip_policy_warnings(trip_dir, expenses, transactions)
    unresolved_policy_warnings = sum(1 for warning in policy_warnings if not warning["resolved"])
    needs_review += unresolved_policy_warnings
    unmatched_expenses = [expense for expense in expenses if expense["source_file"] not in matched_files]
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
                    or (
                        item.get("allocations")
                        and item.get("allocation_status") == "balanced"
                    )
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
        purchase_amount = sum_values([leg.purchase_amount for leg in eligible]) if purchase_currency else None
        completeness = representative.cad_completeness
        cad_amount = sum_values([leg.cad_amount for leg in eligible]) if completeness == "complete" else None
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
                "match_eligible": bool(eligible),
                "expense_file": source_file_key(expense.source_file) if expense else None,
                "suggested_expense_file": source_file_key(suggested.source_file) if suggested else None,
                "match_status": status,
                "match_confidence": max((leg.match_confidence for leg in legs), default=0.0),
                "auto_match_confidence": max((leg.match_confidence for leg in legs), default=0.0),
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
                    {"file": leg.source_file.name, "row": leg.source_row}
                    for leg in legs
                ],
                "funding_legs": [
                    {
                        "funding_leg_id": leg.funding_leg_id,
                        "source_file": leg.source_file.name,
                        "source_row": leg.source_row,
                        "settlement_amount": leg.settlement_amount,
                        "settlement_currency": leg.settlement_currency,
                        "cad_amount": leg.cad_amount,
                        "normalization_status": leg.normalization_status,
                        "review_note": leg.review_note,
                    }
                    for leg in legs
                ],
                "funding_leg_count": len(legs),
            }
        )
    return sorted(result, key=lambda item: (item["transaction_date"] or "9999-99-99", item["description"], item["group_id"]))


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
                    if isinstance(transaction.amount_cad, (int, float)) and transaction.amount_cad < 0
                    else "purchase",
                    direction="in"
                    if isinstance(transaction.amount_cad, (int, float)) and transaction.amount_cad < 0
                    else "out",
                    match_eligible=transaction.amount_cad is not None,
                    purchase_amount=purchase_amount,
                    purchase_currency=purchase_currency,
                    settlement_amount=transaction.amount_cad,
                    settlement_currency="CAD",
                    cad_amount=transaction.amount_cad,
                    cad_completeness="complete" if transaction.amount_cad is not None else "incomplete",
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


def serialized_extraction_status(expense: dict) -> str:
    if expense.get("amount") is None or not expense.get("currency"):
        return "manual"
    if not expense.get("date") or not expense.get("vendor") or expense.get("vendor") == "Unknown supplier":
        return "review"
    return "review" if expense.get("review_note") else "ok"


def expense_label(expense: dict | None) -> str | None:
    if not expense:
        return None
    vendor = expense.get("vendor") or expense.get("source_file") or "Unknown invoice"
    amount = expense.get("amount")
    currency = expense.get("currency") or ""
    amount_label = f"{amount:,.2f} {currency}" if isinstance(amount, (int, float)) else "amount missing"
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


def append_reconciliation_note(existing: str, note: str) -> str:
    return f"{existing.rstrip()} {note}".strip() if existing else note


def reconciliation_input_fingerprint(trip_dir: Path) -> str:
    digest = hashlib.sha256()
    digest.update(trip_mode(trip_dir).encode("utf-8"))
    receipts_folder = trip_receipts_dir(trip_dir)
    for path in list_receipt_files(receipts_folder):
        stat = path.stat()
        source_name = relative_source_name(receipts_folder, path)
        digest.update(f"{receipts_folder.name}/{source_name}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8"))
    statements_folder = trip_statements_dir(trip_dir)
    if statements_folder.exists():
        for path in sorted(
            item for item in statements_folder.iterdir() if item.is_file() and not item.name.startswith(".")
        ):
            stat = path.stat()
            digest.update(f"{statements_folder.name}/{path.name}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8"))
    date_settings = trip_dir / STATEMENT_SETTINGS_FILE
    if date_settings.is_file():
        digest.update(date_settings.read_bytes())
    return digest.hexdigest()


def load_reconciliation_state(trip_dir: Path) -> dict | None:
    path = trip_dir / RECONCILIATION_FILE
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict) or state.get("version") != RECONCILIATION_VERSION:
        return None
    return state


def save_reconciliation_state(trip_dir: Path, state: dict) -> None:
    path = trip_dir / RECONCILIATION_FILE
    temporary = trip_dir / f".{RECONCILIATION_FILE}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def emit_progress(
    callback: ProgressCallback | None,
    stage: str,
    current: int,
    total: int,
    message: str,
) -> None:
    if callback:
        callback(GenerationProgress(stage=stage, current=current, total=total, message=message))
