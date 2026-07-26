from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from difflib import SequenceMatcher
from datetime import date, datetime
from pathlib import Path

from nlp_expenses.models import Expense, LineItem
from nlp_expenses.trips import trip_mode, trip_receipts_dir


LINE_ITEM_REVIEW_FILE = ".nlp-expenses-line-items.json"
LINE_ITEM_REVIEW_VERSION = 1
AUTO_DEACTIVATE_CONFIDENCE = 0.90
EXPENSE_TEXT_FIELDS = {
    "vendor",
    "description",
    "expense_type",
    "currency",
    "country",
    "province",
    "gst_hst_number",
    "qst_number",
    "business_purpose",
    "attendees_client",
    "tax_documentation_status",
    "review_note",
    "manual_cad_note",
}
EXPENSE_NUMERIC_FIELDS = {"amount", "subtotal", "gst_hst", "qst"}
EXPENSE_REVIEW_FIELDS = EXPENSE_TEXT_FIELDS | EXPENSE_NUMERIC_FIELDS | {
    "date",
    "included",
    "number_of_people",
    "manual_cad_override",
}


class ExpenseReviewValidationError(ValueError):
    def __init__(self, fields: dict[str, str]):
        self.fields = fields
        super().__init__("Correct the highlighted expense fields.")


def sync_line_item_review(
    trip_dir: Path,
    root: Path,
    llm_mode: str = "off",
    progress_callback=None,
    warning_callback=None,
    allow_openai_prompt: bool = True,
) -> dict:
    """Extract receipt lines and persist a reviewable snapshot for the browser."""

    from nlp_expenses.generator import extract_trip_expenses

    selected_mode = trip_mode(trip_dir)
    expenses = extract_trip_expenses(
        trip_receipts_dir(trip_dir),
        root,
        selected_mode,
        llm_mode,
        progress_callback=progress_callback,
        warning_callback=warning_callback,
        allow_openai_prompt=allow_openai_prompt,
    )
    save_line_item_review(trip_dir, expenses, llm_mode=llm_mode)
    return line_item_review_view(trip_dir)


def save_line_item_review(
    trip_dir: Path,
    expenses: list[Expense],
    llm_mode: str = "off",
) -> dict:
    """Save extracted lines while preserving decisions for unchanged sources."""

    trip_dir = trip_dir.resolve()
    fingerprint = line_item_input_fingerprint(trip_dir)
    previous = load_line_item_review_state(trip_dir)
    preserve = bool(previous and previous.get("input_fingerprint") == fingerprint)
    previous_items = {
        (receipt.get("source_file"), item.get("line_id")): item
        for receipt in (previous.get("receipts", []) if preserve else [])
        if isinstance(receipt, dict)
        for item in receipt.get("line_items", [])
        if isinstance(item, dict)
    }
    selected_mode = trip_mode(trip_dir)
    previous_receipts = {
        receipt.get("source_file"): receipt
        for receipt in (previous.get("receipts", []) if preserve else [])
        if isinstance(receipt, dict) and receipt.get("source_file")
    }
    receipts = []
    for expense in expenses:
        occurrences: dict[str, int] = {}
        lines = []
        extracted_lines = []
        stored_receipt = previous_receipts.get(expense.source_file.name)
        stored_lines = [
            item
            for item in (stored_receipt.get("line_items", []) if stored_receipt else [])
            if isinstance(item, dict)
        ]
        removed_lines = [
            item
            for item in (stored_receipt.get("removed_line_items", []) if stored_receipt else [])
            if isinstance(item, dict)
        ]
        used_stored_ids: set[str] = set()
        used_removed_ids: set[str] = set()
        removed_ids = {
            item.get("line_id")
            for item in removed_lines
        }
        for item in expense.line_items:
            line_id = stable_line_id(expense.source_file.name, item, occurrences)
            automatic = serialize_line_item(item, line_id, selected_mode)
            extracted_lines.append(dict(automatic))
            stored = previous_items.get((expense.source_file.name, line_id))
            if not stored:
                stored = fuzzy_matching_line(
                    automatic,
                    stored_lines,
                    used_stored_ids,
                    require_user_decision=True,
                )
            if stored:
                automatic = preserve_line_decisions(automatic, stored)
                used_stored_ids.add(str(stored.get("line_id") or ""))
            removed = line_id in removed_ids
            if not removed:
                fuzzy_removed = fuzzy_matching_line(
                    automatic,
                    removed_lines,
                    used_removed_ids,
                    require_user_decision=False,
                )
                if fuzzy_removed:
                    used_removed_ids.add(str(fuzzy_removed.get("line_id") or ""))
                    removed = True
            if not removed:
                lines.append(automatic)
        for stored in stored_lines:
            stored_id = str(stored.get("line_id") or "")
            if (
                stored.get("manual")
                and stored_id not in used_stored_ids
                and stored_id not in removed_ids
            ):
                lines.append(dict(stored))
        extracted = serialize_expense_fields(expense)
        receipt = {
            "source_file": expense.source_file.name,
            "extraction_confidence": expense.confidence,
            "line_items": lines,
            "extracted_line_items": extracted_lines,
            "extracted": extracted,
            "field_overrides": {},
        }
        receipt.update(extracted)
        receipt["receipt_total"] = receipt.get("amount")
        if stored_receipt:
            preserve_expense_decisions(receipt, stored_receipt)
        recompute_receipt(receipt)
        receipts.append(receipt)
    state = {
        "version": LINE_ITEM_REVIEW_VERSION,
        "mode": selected_mode,
        "synced_at": datetime.now().isoformat(timespec="seconds"),
        "quality": "best" if llm_mode == "required" else "basic",
        "input_fingerprint": fingerprint,
        "receipts": receipts,
    }
    save_line_item_review_state(trip_dir, state)
    return state


def line_item_review_view(trip_dir: Path, state: dict | None = None) -> dict:
    state = state if state is not None else load_line_item_review_state(trip_dir)
    if not state:
        return empty_line_item_review()
    receipts = [copy_receipt(receipt) for receipt in state.get("receipts", []) if isinstance(receipt, dict)]
    for receipt in receipts:
        recompute_receipt(receipt)
    stale = state.get("input_fingerprint") != line_item_input_fingerprint(trip_dir)
    all_items = [item for receipt in receipts for item in receipt["line_items"]]
    accounting_items = [
        item for item in all_items if item.get("description") != "Alcohol adjustment - manual"
    ]
    meal_receipts = [receipt for receipt in receipts if receipt.get("expense_type") in {"meal", "meal-breakfast", "meal-lunch", "meal-dinner"}]
    return {
        "available": True,
        "stale": stale,
        "synced_at": state.get("synced_at"),
        "quality": state.get("quality", "basic"),
        "mode": state.get("mode"),
        "receipts": receipts,
        "summary": {
            "receipt_count": len(receipts),
            "meal_receipt_count": len(meal_receipts),
            "line_count": len(all_items),
            "alcohol_count": sum(1 for item in accounting_items if item.get("is_alcohol")),
            "excluded_count": sum(1 for item in accounting_items if not item.get("included", True)),
            "excluded_expense_count": sum(1 for receipt in receipts if not receipt.get("included", True)),
            "review_count": sum(1 for receipt in receipts if receipt.get("status") == "review"),
            "blocking_count": sum(1 for receipt in receipts if receipt.get("blocking")),
            "field_issue_count": sum(len(receipt.get("field_issues", [])) for receipt in receipts),
        },
    }


def set_expense_review(trip_dir: Path, source_file: str, fields: dict) -> dict:
    """Update the canonical, workbook-bound expense record for one receipt."""

    state = require_current_state(trip_dir)
    receipt = find_receipt(state, source_file)
    unknown = set(fields) - EXPENSE_REVIEW_FIELDS
    if unknown:
        raise ValueError(f"Unsupported expense fields: {', '.join(sorted(unknown))}.")
    normalized = validate_expense_fields(fields, receipt)
    extracted = receipt.get("extracted") if isinstance(receipt.get("extracted"), dict) else {}
    overrides = receipt.setdefault("field_overrides", {})
    for field, value in normalized.items():
        receipt[field] = value
        if values_differ(value, extracted.get(field)):
            overrides[field] = value
        else:
            overrides.pop(field, None)
    receipt["receipt_total"] = receipt.get("amount")
    receipt["updated_at"] = datetime.now().isoformat(timespec="seconds")
    recompute_receipt(receipt)
    save_line_item_review_state(trip_dir, state)
    mark_reconciliation_requires_resync(trip_dir)
    return line_item_review_view(trip_dir, state)


def add_line_item(
    trip_dir: Path,
    source_file: str,
    description: str,
    amount: object,
    included: bool = True,
    is_alcohol: bool = False,
) -> dict:
    """Add a user-entered receipt line with an auditable stable identifier."""

    state = require_current_state(trip_dir)
    receipt = find_receipt(state, source_file)
    description = " ".join(str(description or "").split())
    if not description:
        raise ValueError("Line-item description cannot be empty.")
    try:
        numeric_amount = round(float(amount), 2)
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a valid non-negative line-item amount.") from exc
    if numeric_amount < 0:
        raise ValueError("Line-item amount cannot be negative.")
    if not isinstance(included, bool) or not isinstance(is_alcohol, bool):
        raise ValueError("Line-item choices must be true or false.")
    line_id = f"LI-manual-{uuid.uuid4().hex[:16]}"
    receipt.setdefault("line_items", []).append(
        {
            "line_id": line_id,
            "description": description[:160],
            "amount": numeric_amount,
            "is_alcohol": is_alcohol,
            "included": included,
            "auto_is_alcohol": is_alcohol,
            "auto_included": included,
            "extracted_description": description[:160],
            "extracted_amount": numeric_amount,
            "confidence": 1.0,
            "review_note": "Added manually in the trip app.",
            "alcohol_confidence": 1.0 if is_alcohol else 0.0,
            "alcohol_reason": "Classified manually" if is_alcohol else "",
            "alcohol_matched_term": "",
            "auto_alcohol_confidence": 1.0 if is_alcohol else 0.0,
            "auto_alcohol_reason": "Classified manually" if is_alcohol else "",
            "auto_alcohol_matched_term": "",
            "inclusion_overridden": False,
            "alcohol_overridden": False,
            "inclusion_note": "",
            "synthetic": False,
            "manual": True,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    recompute_receipt(receipt)
    save_line_item_review_state(trip_dir, state)
    return line_item_review_view(trip_dir, state)


def remove_line_item(trip_dir: Path, source_file: str, line_id: str) -> dict:
    """Remove a line from the claim review while retaining it in the audit state."""

    state = require_current_state(trip_dir)
    receipt = find_receipt(state, source_file)
    items = receipt.get("line_items", [])
    index = next((index for index, item in enumerate(items) if item.get("line_id") == line_id), None)
    if index is None:
        raise FileNotFoundError("The selected receipt line is no longer available.")
    removed = dict(items.pop(index))
    removed["removed_at"] = datetime.now().isoformat(timespec="seconds")
    receipt.setdefault("removed_line_items", []).append(removed)
    recompute_receipt(receipt)
    save_line_item_review_state(trip_dir, state)
    return line_item_review_view(trip_dir, state)


def set_line_item_review(
    trip_dir: Path,
    source_file: str,
    line_id: str,
    fields: dict,
) -> dict:
    state = require_current_state(trip_dir)
    receipt = find_receipt(state, source_file)
    item = next((value for value in receipt.get("line_items", []) if value.get("line_id") == line_id), None)
    if not item:
        raise FileNotFoundError("The selected receipt line is no longer available.")
    supported = {"included", "is_alcohol", "description", "amount", "note"}
    unknown = set(fields) - supported
    if unknown:
        raise ValueError(f"Unsupported line-item fields: {', '.join(sorted(unknown))}.")
    if "included" in fields:
        if not isinstance(fields["included"], bool):
            raise ValueError("Included must be true or false.")
        item["included"] = fields["included"]
        item["inclusion_overridden"] = item["included"] != item.get("auto_included")
    if "is_alcohol" in fields:
        if not isinstance(fields["is_alcohol"], bool):
            raise ValueError("Alcohol classification must be true or false.")
        item["is_alcohol"] = fields["is_alcohol"]
        item["alcohol_overridden"] = item["is_alcohol"] != item.get("auto_is_alcohol")
        if item["alcohol_overridden"]:
            item["alcohol_reason"] = "Manually corrected by the user"
            item["alcohol_matched_term"] = ""
            item["alcohol_confidence"] = 1.0
        else:
            restore_automatic_alcohol(item)
    if "description" in fields:
        description = " ".join(str(fields["description"] or "").split())
        if not description:
            raise ValueError("Line-item description cannot be empty.")
        item["description"] = description[:160]
    if "amount" in fields:
        try:
            amount = round(float(fields["amount"]), 2)
        except (TypeError, ValueError) as exc:
            raise ValueError("Enter a valid non-negative line-item amount.") from exc
        if amount < 0:
            raise ValueError("Line-item amount cannot be negative.")
        item["amount"] = amount
    if "note" in fields:
        item["inclusion_note"] = str(fields["note"] or "").strip()[:500]
    item["updated_at"] = datetime.now().isoformat(timespec="seconds")
    recompute_receipt(receipt)
    save_line_item_review_state(trip_dir, state)
    return line_item_review_view(trip_dir, state)


def reset_receipt_review(trip_dir: Path, source_file: str) -> dict:
    state = require_current_state(trip_dir)
    receipt = find_receipt(state, source_file)
    extracted = receipt.get("extracted") if isinstance(receipt.get("extracted"), dict) else {}
    receipt.update(extracted)
    receipt["field_overrides"] = {}
    receipt["receipt_total"] = receipt.get("amount")
    baseline_lines = receipt.get("extracted_line_items")
    if isinstance(baseline_lines, list):
        receipt["line_items"] = [dict(item) for item in baseline_lines if isinstance(item, dict)]
    receipt["removed_line_items"] = []
    for item in receipt.get("line_items", []):
        item["description"] = item.get("extracted_description", item.get("description", ""))
        item["amount"] = item.get("extracted_amount")
        item["is_alcohol"] = bool(item.get("auto_is_alcohol"))
        item["included"] = bool(item.get("auto_included", True))
        item["inclusion_overridden"] = False
        item["alcohol_overridden"] = False
        item["inclusion_note"] = ""
        item["updated_at"] = None
        restore_automatic_alcohol(item)
    recompute_receipt(receipt)
    save_line_item_review_state(trip_dir, state)
    return line_item_review_view(trip_dir, state)


def apply_line_item_review(
    trip_dir: Path,
    expenses: list[Expense],
    require_fresh: bool = False,
) -> bool:
    """Apply persisted lines and inclusion decisions to freshly extracted expenses."""

    view = line_item_review_view(trip_dir)
    if not view["available"] or view["stale"]:
        if require_fresh:
            raise ValueError("Review current receipt line items before generating the workbook.")
        return False
    by_file = {receipt["source_file"]: receipt for receipt in view["receipts"]}
    for expense in expenses:
        receipt = by_file.get(expense.source_file.name)
        if not receipt:
            continue
        expense.line_items = [deserialize_line_item(item) for item in receipt["line_items"]]
        apply_expense_fields(expense, receipt)
        expense.review_note = effective_review_note(receipt)
        expense.line_item_review_status = str(receipt.get("status") or "")
        expense.line_item_total = receipt.get("line_total")
        expense.included_line_total = receipt.get("included_total")
        expense.excluded_line_total = receipt.get("excluded_total")
        expense.claimable_ratio = float(receipt.get("claimable_ratio") or 0.0)
        if trip_mode(trip_dir) == "ivado" and expense.included_line_total is not None:
            expense.corrected_amount_in_currency = expense.included_line_total
    return True


def effective_review_note(receipt: dict) -> str:
    """Remove automatic warnings that the persisted app review has resolved."""

    note = str(receipt.get("review_note") or "")
    overrides = receipt.get("field_overrides") if isinstance(receipt.get("field_overrides"), dict) else {}
    if receipt.get("status") in {"ok", "not_applicable"}:
        note = re.sub(
            r"Line items total .*?review line items/alcohol\.\s*",
            "",
            note,
            flags=re.IGNORECASE,
        )
        note = re.sub(
            r"Unreconciled meal item line added to balance receipt total; "
            r"review description/alcohol manually\.\s*",
            "",
            note,
            flags=re.IGNORECASE,
        )
    if receipt.get("amount") is not None and "amount" in overrides:
        note = re.sub(
            r"Missing receipt total; review manually\.\s*",
            "",
            note,
            flags=re.IGNORECASE,
        )
    if receipt.get("currency") and "currency" in overrides:
        note = re.sub(
            r"Currency inferred as .*?; review if receipt is not Australian\.\s*",
            "",
            note,
            flags=re.IGNORECASE,
        )
    return " ".join(note.split())


def ensure_line_item_review_ready(trip_dir: Path) -> dict:
    view = line_item_review_view(trip_dir)
    if not view["available"] or view["stale"]:
        raise ValueError("Scan and review the current receipt line items before generating.")
    if view["summary"]["blocking_count"]:
        raise ValueError(
            "Resolve line-item totals before generating; excluded lines require a receipt that reconciles to its total."
        )
    return view


def recompute_receipt(receipt: dict) -> None:
    items = [item for item in receipt.get("line_items", []) if isinstance(item, dict)]
    receipt["line_items"] = items
    values = [float(item["amount"]) for item in items if isinstance(item.get("amount"), (int, float))]
    included_values = [
        float(item["amount"])
        for item in items
        if item.get("included", True) and isinstance(item.get("amount"), (int, float))
    ]
    line_total = round(sum(values), 2)
    included_total = round(sum(included_values), 2)
    excluded_total = round(line_total - included_total, 2)
    receipt_total = receipt.get("amount", receipt.get("receipt_total"))
    receipt["receipt_total"] = receipt_total
    difference = (
        round(line_total - float(receipt_total), 2)
        if isinstance(receipt_total, (int, float))
        else None
    )
    tolerance = max(0.05, abs(float(receipt_total or 0)) * 0.03)
    is_meal = str(receipt.get("expense_type") or "").startswith("meal")
    has_gap = any(item.get("description") == "Unreconciled meal item - review" for item in items)
    reconciled = difference is not None and abs(difference) <= tolerance
    if not is_meal:
        status = "not_applicable"
    elif not reconciled or has_gap:
        status = "review"
    else:
        status = "ok"
    has_exclusions = excluded_total > 0.005
    blocking = bool(is_meal and has_exclusions and (not reconciled or has_gap))
    claimable_ratio = (
        round(max(0.0, min(1.0, included_total / line_total)), 8)
        if has_exclusions and reconciled and line_total > 0
        else 1.0
    )
    receipt.update(
        {
            "line_total": line_total,
            "included_total": included_total,
            "excluded_total": excluded_total,
            "difference": difference,
            "reconciled": reconciled,
            "status": status,
            "blocking": blocking,
            "claimable_ratio": claimable_ratio,
            "field_issues": expense_field_issues(receipt),
        }
    )


def serialize_line_item(item: LineItem, line_id: str, mode: str) -> dict:
    auto_included = mode != "ivado" or not (
        item.is_alcohol and item.alcohol_confidence >= AUTO_DEACTIVATE_CONFIDENCE
    )
    return {
        "line_id": line_id,
        "description": item.description,
        "amount": item.amount,
        "is_alcohol": bool(item.is_alcohol),
        "included": auto_included,
        "auto_is_alcohol": bool(item.is_alcohol),
        "auto_included": auto_included,
        "extracted_description": item.description,
        "extracted_amount": item.amount,
        "confidence": item.confidence,
        "review_note": item.review_note,
        "alcohol_confidence": item.alcohol_confidence,
        "alcohol_reason": item.alcohol_reason,
        "alcohol_matched_term": item.alcohol_matched_term,
        "auto_alcohol_confidence": item.alcohol_confidence,
        "auto_alcohol_reason": item.alcohol_reason,
        "auto_alcohol_matched_term": item.alcohol_matched_term,
        "inclusion_overridden": False,
        "alcohol_overridden": False,
        "inclusion_note": "",
        "synthetic": bool(item.synthetic),
        "updated_at": None,
    }


def preserve_line_decisions(automatic: dict, stored: dict) -> dict:
    if stored.get("inclusion_overridden"):
        automatic["included"] = bool(stored.get("included"))
        automatic["inclusion_overridden"] = True
        automatic["inclusion_note"] = str(stored.get("inclusion_note") or "")
    if stored.get("alcohol_overridden"):
        automatic["is_alcohol"] = bool(stored.get("is_alcohol"))
        automatic["alcohol_overridden"] = True
        automatic["alcohol_confidence"] = 1.0
        automatic["alcohol_reason"] = "Manually corrected by the user"
        automatic["alcohol_matched_term"] = ""
    automatic["description"] = str(stored.get("description") or automatic["description"])
    if isinstance(stored.get("amount"), (int, float)):
        automatic["amount"] = float(stored["amount"])
    if stored.get("manual"):
        automatic["manual"] = True
    automatic["updated_at"] = stored.get("updated_at")
    return automatic


def fuzzy_matching_line(
    automatic: dict,
    candidates: list[dict],
    used_ids: set[str],
    *,
    require_user_decision: bool,
) -> dict | None:
    """Match a reviewed line across nondeterministic OCR/LLM wording changes."""

    best: tuple[float, dict] | None = None
    for candidate in candidates:
        candidate_id = str(candidate.get("line_id") or "")
        if candidate_id in used_ids:
            continue
        if require_user_decision and not line_has_user_decision(candidate):
            continue
        score = fuzzy_line_score(automatic, candidate)
        if score >= 0.70 and (best is None or score > best[0]):
            best = (score, candidate)
    return best[1] if best else None


def line_has_user_decision(item: dict) -> bool:
    return bool(
        item.get("manual")
        or item.get("inclusion_overridden")
        or item.get("alcohol_overridden")
        or item.get("updated_at")
    )


def fuzzy_line_score(first: dict, second: dict) -> float:
    first_description = normalized_line_description(first.get("description"))
    second_description = normalized_line_description(second.get("description"))
    if not first_description or not second_description:
        return 0.0
    description_score = SequenceMatcher(None, first_description, second_description).ratio()
    first_amount = first.get("amount")
    second_amount = second.get("amount")
    if isinstance(first_amount, (int, float)) and isinstance(second_amount, (int, float)):
        tolerance = max(0.10, max(abs(float(first_amount)), abs(float(second_amount))) * 0.03)
        amount_score = 1.0 if abs(float(first_amount) - float(second_amount)) <= tolerance else 0.0
    else:
        amount_score = 0.25 if first_amount is None and second_amount is None else 0.0
    alcohol_score = 1.0 if bool(first.get("is_alcohol")) == bool(second.get("is_alcohol")) else 0.0
    return 0.65 * description_score + 0.30 * amount_score + 0.05 * alcohol_score


def normalized_line_description(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def serialize_expense_fields(expense: Expense) -> dict:
    return {
        "date": expense.date,
        "vendor": expense.supplier_name,
        "description": expense.description,
        "expense_type": expense.expense_type,
        "amount": expense.amount,
        "currency": expense.currency,
        "country": expense.country,
        "province": expense.province,
        "subtotal": expense.subtotal,
        "gst_hst": expense.gst_hst,
        "qst": expense.qst,
        "gst_hst_number": expense.gst_hst_number,
        "qst_number": expense.qst_number,
        "business_purpose": expense.business_purpose,
        "attendees_client": expense.attendees_client,
        "tax_documentation_status": expense.tax_documentation_status,
        "review_note": expense.review_note,
        "manual_cad_override": expense.manual_cad_override,
        "manual_cad_note": expense.manual_cad_note,
        "included": bool(expense.included),
        "number_of_people": max(1, int(expense.number_of_people or 1)),
    }


def preserve_expense_decisions(automatic: dict, stored: dict) -> None:
    extracted = automatic["extracted"]
    old_extracted = stored.get("extracted") if isinstance(stored.get("extracted"), dict) else {}
    overrides = stored.get("field_overrides") if isinstance(stored.get("field_overrides"), dict) else {}
    if not overrides:
        # Migration path for review snapshots created before expense-level editing.
        for field in EXPENSE_REVIEW_FIELDS:
            if field in stored and values_differ(stored.get(field), old_extracted.get(field, extracted.get(field))):
                overrides[field] = stored.get(field)
    for field, value in overrides.items():
        if field in EXPENSE_REVIEW_FIELDS:
            automatic[field] = value
    automatic["field_overrides"] = dict(overrides)
    automatic["removed_line_items"] = [
        dict(item) for item in stored.get("removed_line_items", []) if isinstance(item, dict)
    ]
    automatic["updated_at"] = stored.get("updated_at")


def validate_expense_fields(fields: dict, current: dict) -> dict:
    errors: dict[str, str] = {}
    normalized: dict = {}
    for field, value in fields.items():
        if field in EXPENSE_TEXT_FIELDS:
            normalized[field] = " ".join(str(value or "").split())[:500]
        elif field in EXPENSE_NUMERIC_FIELDS:
            if value in ("", None):
                normalized[field] = None
            else:
                try:
                    normalized[field] = round(float(value), 2)
                except (TypeError, ValueError):
                    errors[field] = "Enter a valid number."
        elif field == "date":
            normalized[field] = str(value or "").strip() or None
            if normalized[field]:
                try:
                    date.fromisoformat(normalized[field])
                except ValueError:
                    errors[field] = "Use a valid date in YYYY-MM-DD format."
        elif field == "included":
            if not isinstance(value, bool):
                errors[field] = "Included must be true or false."
            else:
                normalized[field] = value
        elif field == "number_of_people":
            try:
                people = int(value)
            except (TypeError, ValueError):
                people = 0
            if people < 1 or people > 99:
                errors[field] = "Enter a whole number from 1 to 99."
            else:
                normalized[field] = people
        elif field == "manual_cad_override":
            if value in ("", None):
                normalized[field] = None
            else:
                try:
                    manual_cad = round(float(value), 2)
                except (TypeError, ValueError):
                    manual_cad = 0.0
                if manual_cad <= 0:
                    errors[field] = "The manual CAD amount must be greater than zero."
                else:
                    normalized[field] = manual_cad
    if "currency" in normalized:
        normalized["currency"] = normalized["currency"].upper()
        if normalized["currency"] and (
            len(normalized["currency"]) != 3 or not normalized["currency"].isalpha()
        ):
            errors["currency"] = "Use a three-letter currency code such as CAD or AUD."
    amount = normalized.get("amount", current.get("amount"))
    for field in EXPENSE_NUMERIC_FIELDS:
        value = normalized.get(field, current.get(field))
        if value is not None and value < 0:
            errors[field] = "Amount cannot be negative."
    taxes = sum(normalized.get(field, current.get(field)) or 0 for field in ("gst_hst", "qst"))
    if amount is not None and taxes > amount:
        errors["gst_hst"] = "Combined tax cannot be greater than the receipt total."
        errors["qst"] = "Combined tax cannot be greater than the receipt total."
    manual_cad = normalized.get("manual_cad_override", current.get("manual_cad_override"))
    manual_note = normalized.get("manual_cad_note", current.get("manual_cad_note"))
    if manual_cad is not None and not str(manual_note or "").strip():
        errors["manual_cad_note"] = "Explain the source of the manual CAD amount."
    if errors:
        raise ExpenseReviewValidationError(errors)
    return normalized


def apply_expense_fields(expense: Expense, receipt: dict) -> None:
    mapping = {
        "date": "date",
        "vendor": "supplier_name",
        "description": "description",
        "expense_type": "expense_type",
        "amount": "amount",
        "currency": "currency",
        "country": "country",
        "province": "province",
        "subtotal": "subtotal",
        "gst_hst": "gst_hst",
        "qst": "qst",
        "gst_hst_number": "gst_hst_number",
        "qst_number": "qst_number",
        "business_purpose": "business_purpose",
        "attendees_client": "attendees_client",
        "tax_documentation_status": "tax_documentation_status",
        "review_note": "review_note",
        "manual_cad_override": "manual_cad_override",
        "manual_cad_note": "manual_cad_note",
        "included": "included",
        "number_of_people": "number_of_people",
    }
    for field, attribute in mapping.items():
        if field in receipt:
            setattr(expense, attribute, receipt[field])


def expense_field_issues(receipt: dict) -> list[str]:
    labels = {
        "date": "date",
        "vendor": "vendor",
        "amount": "total",
        "currency": "currency",
        "expense_type": "expense type",
    }
    return [
        f"Missing {label}"
        for field, label in labels.items()
        if receipt.get(field) in (None, "", "Unknown supplier")
    ]


def values_differ(reviewed, extracted) -> bool:
    if reviewed in (None, "") and extracted in (None, ""):
        return False
    return reviewed != extracted


def mark_reconciliation_requires_resync(trip_dir: Path) -> None:
    try:
        from nlp_expenses.reconciliation import load_reconciliation_state, save_reconciliation_state

        state = load_reconciliation_state(trip_dir)
        if state:
            state["requires_resync"] = True
            save_reconciliation_state(trip_dir, state)
    except Exception:
        # The receipt review remains valid even if an old reconciliation file is unreadable.
        return


def restore_automatic_alcohol(item: dict) -> None:
    item["alcohol_confidence"] = item.get("auto_alcohol_confidence", 0.0)
    item["alcohol_reason"] = item.get("auto_alcohol_reason", "")
    item["alcohol_matched_term"] = item.get("auto_alcohol_matched_term", "")


def deserialize_line_item(item: dict) -> LineItem:
    return LineItem(
        description=str(item.get("description") or ""),
        amount=float(item["amount"]) if isinstance(item.get("amount"), (int, float)) else None,
        is_alcohol=bool(item.get("is_alcohol")),
        included=bool(item.get("included", True)),
        confidence=float(item.get("confidence") or 0.0),
        review_note=str(item.get("review_note") or ""),
        alcohol_confidence=float(item.get("alcohol_confidence") or 0.0),
        alcohol_reason=str(item.get("alcohol_reason") or ""),
        alcohol_matched_term=str(item.get("alcohol_matched_term") or ""),
        line_id=str(item.get("line_id") or ""),
        inclusion_overridden=bool(item.get("inclusion_overridden")),
        alcohol_overridden=bool(item.get("alcohol_overridden")),
        inclusion_note=str(item.get("inclusion_note") or ""),
        synthetic=bool(item.get("synthetic")),
    )


def stable_line_id(source_file: str, item: LineItem, occurrences: dict[str, int]) -> str:
    description = re.sub(r"\s+", " ", item.description.strip().casefold())
    amount = "" if item.amount is None else f"{item.amount:.2f}"
    signature = f"{description}|{amount}"
    occurrence = occurrences.get(signature, 0) + 1
    occurrences[signature] = occurrence
    digest = hashlib.sha256(f"{source_file}|{signature}|{occurrence}".encode("utf-8")).hexdigest()[:16]
    return f"LI-{digest}"


def line_item_input_fingerprint(trip_dir: Path) -> str:
    digest = hashlib.sha256()
    digest.update(trip_mode(trip_dir).encode("utf-8"))
    folder = trip_receipts_dir(trip_dir)
    if folder.exists():
        for path in sorted(item for item in folder.iterdir() if item.is_file() and not item.name.startswith(".")):
            digest.update(path.name.encode("utf-8"))
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def load_line_item_review_state(trip_dir: Path) -> dict | None:
    path = trip_dir / LINE_ITEM_REVIEW_FILE
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict) or state.get("version") != LINE_ITEM_REVIEW_VERSION:
        return None
    return state


def save_line_item_review_state(trip_dir: Path, state: dict) -> None:
    path = trip_dir / LINE_ITEM_REVIEW_FILE
    temporary = trip_dir / f".{LINE_ITEM_REVIEW_FILE}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def require_current_state(trip_dir: Path) -> dict:
    state = load_line_item_review_state(trip_dir)
    if not state or state.get("input_fingerprint") != line_item_input_fingerprint(trip_dir):
        raise ValueError("Receipt files changed after the last line-item scan. Scan again before editing.")
    return state


def find_receipt(state: dict, source_file: str) -> dict:
    if not source_file or source_file != Path(source_file).name:
        raise ValueError("Invalid receipt filename.")
    receipt = next((value for value in state.get("receipts", []) if value.get("source_file") == source_file), None)
    if not receipt:
        raise FileNotFoundError("The selected receipt is no longer available.")
    return receipt


def copy_receipt(receipt: dict) -> dict:
    copied = dict(receipt)
    copied.setdefault("amount", copied.get("receipt_total"))
    copied.setdefault("description", "")
    copied.setdefault("country", "")
    copied.setdefault("province", "")
    copied.setdefault("subtotal", None)
    copied.setdefault("gst_hst", None)
    copied.setdefault("qst", None)
    copied.setdefault("gst_hst_number", "")
    copied.setdefault("qst_number", "")
    copied.setdefault("business_purpose", "")
    copied.setdefault("attendees_client", "")
    copied.setdefault("tax_documentation_status", "")
    copied.setdefault("included", True)
    copied.setdefault("number_of_people", 1)
    copied.setdefault("manual_cad_override", None)
    copied.setdefault("manual_cad_note", "")
    copied["line_items"] = [dict(item) for item in receipt.get("line_items", []) if isinstance(item, dict)]
    extracted = dict(receipt.get("extracted", {}))
    for field in EXPENSE_REVIEW_FIELDS:
        extracted.setdefault(field, copied.get(field))
    copied["extracted"] = extracted
    copied["field_overrides"] = dict(receipt.get("field_overrides", {}))
    copied["overridden_fields"] = sorted(copied["field_overrides"])
    copied.pop("extracted_line_items", None)
    copied.pop("removed_line_items", None)
    return copied


def empty_line_item_review() -> dict:
    return {
        "available": False,
        "stale": False,
        "synced_at": None,
        "quality": None,
        "mode": None,
        "receipts": [],
        "summary": {
            "receipt_count": 0,
            "meal_receipt_count": 0,
            "line_count": 0,
            "alcohol_count": 0,
            "excluded_count": 0,
            "excluded_expense_count": 0,
            "review_count": 0,
            "blocking_count": 0,
            "field_issue_count": 0,
        },
    }
