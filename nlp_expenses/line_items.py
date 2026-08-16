from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

from nlp_expenses.currencies import CURRENCY_CODES
from nlp_expenses.models import Expense, LineItem
from nlp_expenses.storage import write_json_atomic
from nlp_expenses.tax_lines import SYSTEM_TAX_LINES, tax_line_type
from nlp_expenses.trip_metadata import PAID_BY_VALUES, normalize_paid_by
from nlp_expenses.trips import (
    list_receipt_files,
    relative_source_name,
    source_file_key,
    trip_mode,
    trip_receipts_dir,
    validate_source_name,
)

LINE_ITEM_REVIEW_FILE = ".nlp-expenses-line-items.json"
LINE_ITEM_REVIEW_VERSION = 5
IVADO_EXCLUSION_REASONS = {
    "alcohol",
    "non_business",
    "policy_cap",
    "missing_documentation",
    "other",
}
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
EXPENSE_REVIEW_FIELDS = (
    EXPENSE_TEXT_FIELDS
    | EXPENSE_NUMERIC_FIELDS
    | {
        "date",
        "included",
        "included_in_arvine",
        "included_in_ivado",
        "ivado_exclusion_reason",
        "paid_by",
        "number_of_people",
        "manual_cad_override",
        "reviewed",
    }
)


class ExpenseReviewValidationError(ValueError):
    def __init__(self, fields: dict[str, str]):
        self.fields = fields
        super().__init__("Correct the highlighted expense fields.")


def sync_line_item_review(
    trip_dir: Path,
    root: Path,
    llm_mode: str = "off",
    only_unscanned: bool = False,
    progress_callback=None,
    warning_callback=None,
    allow_openai_prompt: bool = True,
) -> dict:
    """Extract receipt lines and persist a reviewable snapshot for the browser."""

    from nlp_expenses.generator import extract_trip_expenses

    selected_mode = trip_mode(trip_dir)
    source_files = None
    if only_unscanned:
        scan = receipt_scan_status(trip_dir)
        source_files = {
            item["source_file"] for item in scan["receipts"] if item["status"] != "scanned"
        }
        if not source_files:
            return line_item_review_view(trip_dir)
    expenses = extract_trip_expenses(
        trip_receipts_dir(trip_dir),
        root,
        selected_mode,
        llm_mode,
        source_files=source_files,
        progress_callback=progress_callback,
        warning_callback=warning_callback,
        allow_openai_prompt=allow_openai_prompt,
    )
    state = save_line_item_review(
        trip_dir,
        expenses,
        llm_mode=llm_mode,
        merge_existing=only_unscanned,
    )
    refresh_reconciliation_after_receipt_scan(trip_dir, state.get("receipts", []))
    return line_item_review_view(trip_dir)


def save_line_item_review(
    trip_dir: Path,
    expenses: list[Expense],
    llm_mode: str = "off",
    merge_existing: bool = False,
) -> dict:
    """Save extracted lines while preserving decisions for unchanged sources."""

    trip_dir = trip_dir.resolve()
    fingerprint = line_item_input_fingerprint(trip_dir)
    previous = load_line_item_review_state(trip_dir)
    preserve = bool(previous and previous.get("mode") == trip_mode(trip_dir))
    selected_mode = trip_mode(trip_dir)
    from nlp_expenses.trip_metadata import trip_metadata

    default_paid_by = trip_metadata(trip_dir)["default_paid_by"]
    previous_receipts = {
        receipt.get("source_file"): receipt
        for receipt in (previous.get("receipts", []) if preserve else [])
        if isinstance(receipt, dict) and receipt.get("source_file")
    }
    receipts = []
    scanned_at = datetime.now().isoformat(timespec="seconds")
    for expense in expenses:
        normalize_expense_tax_lines(expense)
        source_file = source_file_key(expense.source_file)
        occurrences: dict[str, int] = {}
        lines = []
        extracted_lines = []
        source_path = trip_receipts_dir(trip_dir) / source_file
        stored_receipt = previous_receipts.get(source_file)
        if stored_receipt and not stored_receipt_matches_source(
            stored_receipt, source_path, previous
        ):
            stored_receipt = None
        previous_items = {
            (source_file, item.get("line_id")): item
            for item in (stored_receipt.get("line_items", []) if stored_receipt else [])
            if isinstance(item, dict)
        }
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
        removed_ids = {item.get("line_id") for item in removed_lines}
        for item in expense.line_items:
            line_id = stable_line_id(source_file, item, occurrences)
            automatic = serialize_line_item(item, line_id, selected_mode)
            extracted_lines.append(dict(automatic))
            stored = previous_items.get((source_file, line_id))
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
            "receipt_id": stable_receipt_id(trip_dir.name, source_file),
            "source_file": source_file,
            "review_mode": selected_mode,
            "extraction_confidence": expense.confidence,
            "line_items": lines,
            "extracted_line_items": extracted_lines,
            "extracted": extracted,
            "field_overrides": {},
            "reviewed": False,
            "reviewed_at": None,
            "source_fingerprint": receipt_source_fingerprint(source_path),
            "quality": "best" if llm_mode == "required" else "basic",
            "scanned_at": scanned_at,
        }
        receipt.update(extracted)
        receipt["included_in_arvine"] = True
        receipt["included_in_ivado"] = bool(extracted.get("included", True))
        receipt["paid_by"] = default_paid_by
        receipt["auto_paid_by"] = default_paid_by
        receipt["paid_by_overridden"] = False
        receipt["receipt_total"] = receipt.get("amount")
        if stored_receipt:
            preserve_expense_decisions(receipt, stored_receipt)
        recompute_receipt(receipt, selected_mode)
        receipts.append(receipt)
    if merge_existing and previous_receipts:
        scanned_sources = {receipt["source_file"] for receipt in receipts}
        current_sources = {
            relative_source_name(trip_receipts_dir(trip_dir), path)
            for path in list_receipt_files(trip_receipts_dir(trip_dir))
        }
        receipts = [
            dict(receipt)
            for source_file, receipt in previous_receipts.items()
            if source_file in current_sources and source_file not in scanned_sources
        ] + receipts
        receipts.sort(key=lambda receipt: str(receipt.get("source_file") or "").casefold())
    state = {
        "version": LINE_ITEM_REVIEW_VERSION,
        "mode": selected_mode,
        "synced_at": scanned_at,
        "quality": "best" if llm_mode == "required" else "basic",
        "input_fingerprint": fingerprint,
        "receipts": receipts,
    }
    save_line_item_review_state(trip_dir, state)
    return state


def normalize_expense_tax_lines(expense: Expense) -> None:
    """Normalize parser output before assigning stable review-line identities."""

    ordinary_lines = []
    extracted_tax_lines: dict[str, LineItem] = {}
    for item in expense.line_items:
        kind = (
            item.line_type
            if item.line_type in SYSTEM_TAX_LINES
            else tax_line_type(item.description)
        )
        if kind:
            extracted_tax_lines.setdefault(kind, item)
        else:
            ordinary_lines.append(item)
    tax_lines = []
    for kind, label in SYSTEM_TAX_LINES.items():
        structured_amount = getattr(expense, kind)
        extracted_amount = (
            extracted_tax_lines.get(kind).amount if extracted_tax_lines.get(kind) else None
        )
        amount = round(
            float(structured_amount if structured_amount is not None else extracted_amount or 0), 2
        )
        setattr(expense, kind, amount)
        tax_lines.append(
            LineItem(
                description=label,
                amount=amount,
                line_type=kind,
                included=True,
                confidence=1.0,
                review_note="System tax line synchronized with the expense tax field.",
                synthetic=True,
            )
        )
    expense.line_items = ordinary_lines + tax_lines


def line_item_review_view(trip_dir: Path, state: dict | None = None) -> dict:
    state = state if state is not None else load_line_item_review_state(trip_dir)
    if not state:
        return empty_line_item_review()
    receipts = [
        copy_receipt(receipt) for receipt in state.get("receipts", []) if isinstance(receipt, dict)
    ]
    for receipt in receipts:
        receipt.setdefault("review_mode", state.get("mode") or "company")
        migrate_receipt_contract_fields(receipt, state.get("mode") or "company")
        recompute_receipt(receipt, state.get("mode") or "company")
    stale = state.get("input_fingerprint") != line_item_input_fingerprint(trip_dir)
    all_items = [item for receipt in receipts for item in receipt["line_items"]]
    accounting_items = [
        item for item in all_items if item.get("description") != "Alcohol adjustment - manual"
    ]
    meal_receipts = [
        receipt
        for receipt in receipts
        if receipt.get("expense_type") in {"meal", "meal-breakfast", "meal-lunch", "meal-dinner"}
    ]
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
            "excluded_count": sum(
                1 for item in accounting_items if not item.get("included_in_ivado", True)
            ),
            "arvine_excluded_count": sum(
                1 for item in accounting_items if not item.get("included_in_arvine", True)
            ),
            "excluded_expense_count": sum(
                1 for receipt in receipts if not receipt.get("included_in_arvine", True)
            ),
            "reviewed_count": sum(1 for receipt in receipts if receipt.get("reviewed")),
            "review_count": sum(1 for receipt in receipts if receipt.get("status") == "review"),
            "ok_count": sum(1 for receipt in receipts if receipt.get("status") == "ok"),
            "ready_count": sum(1 for receipt in receipts if receipt.get("status") == "ready"),
            "line_review_count": sum(1 for item in all_items if not item.get("reviewed")),
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
    if set(normalized) - {"reviewed"}:
        receipt["reviewed"] = False
        receipt["reviewed_at"] = None
    if normalized.get("included_in_arvine") is False:
        raise ValueError(
            "The company report includes every uploaded receipt. Remove the receipt file if it does not belong to the trip."
        )
    if state.get("mode") == "company" and normalized.get("included") is False:
        raise ValueError(
            "The company report includes every uploaded receipt. Remove the receipt file if it does not belong to the trip."
        )
    extracted = receipt.get("extracted") if isinstance(receipt.get("extracted"), dict) else {}
    if "expense_type" in normalized:
        current_type = str(receipt.get("expense_type") or "other")
        updated_type = str(normalized["expense_type"] or "other")
        if updated_type.startswith("meal"):
            if not current_type.startswith("meal"):
                receipt["non_meal_expense_type"] = current_type
        else:
            receipt["non_meal_expense_type"] = updated_type
    overrides = receipt.setdefault("field_overrides", {})
    for field, value in normalized.items():
        receipt[field] = value
        if field == "reviewed":
            receipt["reviewed_at"] = datetime.now().isoformat(timespec="seconds") if value else None
            if value:
                for item in receipt.get("line_items", []):
                    item["reviewed"] = True
                    item["reviewed_at"] = receipt["reviewed_at"]
        if field == "included":
            target = "included_in_ivado" if state.get("mode") == "ivado" else "included_in_arvine"
            receipt[target] = value
        if field == "paid_by":
            receipt["paid_by_overridden"] = value != receipt.get(
                "auto_paid_by", "traveller_personal"
            )
        if field == "reviewed":
            overrides.pop(field, None)
            continue
        if values_differ(value, extracted.get(field)):
            overrides[field] = value
        else:
            overrides.pop(field, None)
    receipt["included_in_arvine"] = True
    overrides.pop("included_in_arvine", None)
    if receipt.get("included_in_ivado", True):
        receipt["ivado_exclusion_reason"] = None
    elif not receipt.get("ivado_exclusion_reason"):
        receipt["ivado_exclusion_reason"] = "other"
    receipt["receipt_total"] = receipt.get("amount")
    receipt["updated_at"] = datetime.now().isoformat(timespec="seconds")
    synchronize_receipt_aliases(receipt, state.get("mode") or "company")
    recompute_receipt(receipt, state.get("mode") or "company")
    save_line_item_review_state(trip_dir, state)
    persist_expense_to_reconciliation(trip_dir, receipt, set(normalized))
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
    if tax_line_type(description):
        raise ValueError(
            "Use Edit expense to set GST/HST or QST; tax rows are managed automatically."
        )
    try:
        numeric_amount = round(float(amount), 2)
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a valid line-item amount.") from exc
    if not math.isfinite(numeric_amount):
        raise ValueError("Enter a valid line-item amount.")
    if not isinstance(included, bool) or not isinstance(is_alcohol, bool):
        raise ValueError("Line-item choices must be true or false.")
    is_alcohol = is_alcohol if state.get("mode") == "ivado" and numeric_amount >= 0 else False
    line_id = f"LI-manual-{uuid.uuid4().hex[:16]}"
    receipt.setdefault("line_items", []).append(
        {
            "line_id": line_id,
            "description": description[:160],
            "amount": numeric_amount,
            "is_alcohol": is_alcohol,
            "included": included,
            "included_in_arvine": True,
            "included_in_ivado": included and not is_alcohol,
            "ivado_exclusion_reason": "alcohol" if is_alcohol else None,
            "auto_is_alcohol": is_alcohol,
            "auto_included": included,
            "auto_included_in_arvine": True,
            "auto_included_in_ivado": included and not is_alcohol,
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
            "reviewed": False,
            "reviewed_at": None,
        }
    )
    receipt["reviewed"] = False
    receipt["reviewed_at"] = None
    synchronize_receipt_aliases(receipt, state.get("mode") or "company")
    recompute_receipt(receipt, state.get("mode") or "company")
    save_line_item_review_state(trip_dir, state)
    return line_item_review_view(trip_dir, state)


def remove_line_item(trip_dir: Path, source_file: str, line_id: str) -> dict:
    """Remove a line from the claim review while retaining it in the audit state."""

    state = require_current_state(trip_dir)
    receipt = find_receipt(state, source_file)
    items = receipt.get("line_items", [])
    index = next(
        (index for index, item in enumerate(items) if item.get("line_id") == line_id), None
    )
    if index is None:
        raise FileNotFoundError("The selected receipt line is no longer available.")
    if items[index].get("system_type") in SYSTEM_TAX_LINES:
        raise ValueError("GST/HST and QST are required system rows and cannot be removed.")
    removed = dict(items.pop(index))
    removed["removed_at"] = datetime.now().isoformat(timespec="seconds")
    receipt.setdefault("removed_line_items", []).append(removed)
    receipt["reviewed"] = False
    receipt["reviewed_at"] = None
    recompute_receipt(receipt, state.get("mode") or "company")
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
    item = next(
        (value for value in receipt.get("line_items", []) if value.get("line_id") == line_id), None
    )
    if not item:
        raise FileNotFoundError("The selected receipt line is no longer available.")
    system_type = item.get("system_type")
    supported = {
        "included",
        "included_in_arvine",
        "included_in_ivado",
        "ivado_exclusion_reason",
        "is_alcohol",
        "description",
        "amount",
        "note",
        "reviewed",
    }
    unknown = set(fields) - supported
    if unknown:
        raise ValueError(f"Unsupported line-item fields: {', '.join(sorted(unknown))}.")
    if system_type in SYSTEM_TAX_LINES and set(fields) - {"amount", "reviewed"}:
        raise ValueError(
            "Tax-row labels and claim settings are managed automatically; only the amount is editable."
        )
    if set(fields) - {"reviewed"}:
        item["reviewed"] = False
        item["reviewed_at"] = None
        receipt["reviewed"] = False
        receipt["reviewed_at"] = None
    if "included" in fields:
        if not isinstance(fields["included"], bool):
            raise ValueError("Included must be true or false.")
        item["included"] = fields["included"]
        item["inclusion_overridden"] = item["included"] != item.get("auto_included")
        target = "included_in_ivado" if state.get("mode") == "ivado" else "included_in_arvine"
        if target == "included_in_arvine" and not fields["included"]:
            raise ValueError(
                "The company report includes every receipt line. Remove an incorrect extracted line instead."
            )
        if target == "included_in_ivado" and fields["included"] and item.get("is_alcohol"):
            raise ValueError(
                "Alcohol is excluded from IVADO. Mark the line as not alcohol before including it."
            )
        item[target] = fields["included"]
    for field in ("included_in_arvine", "included_in_ivado"):
        if field in fields:
            if not isinstance(fields[field], bool):
                raise ValueError(f"{field} must be true or false.")
            if field == "included_in_arvine" and not fields[field]:
                raise ValueError(
                    "The company report includes every receipt line. Remove an incorrect extracted line instead."
                )
            if field == "included_in_ivado" and fields[field] and item.get("is_alcohol"):
                raise ValueError(
                    "Alcohol is excluded from IVADO. Mark the line as not alcohol before including it."
                )
            item[field] = fields[field]
            item[f"{field}_overridden"] = item[field] != item.get(f"auto_{field}", True)
    if "ivado_exclusion_reason" in fields:
        reason = str(fields["ivado_exclusion_reason"] or "").strip() or None
        if reason not in IVADO_EXCLUSION_REASONS | {None}:
            raise ValueError("Choose a valid IVADO exclusion reason.")
        item["ivado_exclusion_reason"] = reason
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
    normalize_line_program_decisions(item)
    if "description" in fields:
        description = " ".join(str(fields["description"] or "").split())
        if not description:
            raise ValueError("Line-item description cannot be empty.")
        item["description"] = description[:160]
    if "amount" in fields:
        try:
            amount = round(float(fields["amount"]), 2)
        except (TypeError, ValueError) as exc:
            raise ValueError("Enter a valid line-item amount.") from exc
        if not math.isfinite(amount):
            raise ValueError("Enter a valid line-item amount.")
        if system_type in SYSTEM_TAX_LINES and amount < 0:
            raise ValueError("Tax amount cannot be negative.")
        item["amount"] = amount
        if amount < 0:
            item["is_alcohol"] = False
            item["alcohol_overridden"] = bool(item.get("auto_is_alcohol"))
            item["alcohol_reason"] = "Receipt discount or promotion"
            item["alcohol_matched_term"] = ""
            item["alcohol_confidence"] = 1.0
            normalize_line_program_decisions(item)
        if system_type in SYSTEM_TAX_LINES:
            receipt[system_type] = amount
            extracted = (
                receipt.get("extracted") if isinstance(receipt.get("extracted"), dict) else {}
            )
            overrides = receipt.setdefault("field_overrides", {})
            if values_differ(amount, extracted.get(system_type)):
                overrides[system_type] = amount
            else:
                overrides.pop(system_type, None)
    if "note" in fields:
        item["inclusion_note"] = str(fields["note"] or "").strip()[:500]
    if "reviewed" in fields:
        if not isinstance(fields["reviewed"], bool):
            raise ValueError("Reviewed must be true or false.")
        item["reviewed"] = fields["reviewed"]
        item["reviewed_at"] = (
            datetime.now().isoformat(timespec="seconds") if item["reviewed"] else None
        )
        if not item["reviewed"]:
            receipt["reviewed"] = False
            receipt["reviewed_at"] = None
    item["updated_at"] = datetime.now().isoformat(timespec="seconds")
    synchronize_receipt_aliases(receipt, state.get("mode") or "company")
    recompute_receipt(receipt, state.get("mode") or "company")
    save_line_item_review_state(trip_dir, state)
    return line_item_review_view(trip_dir, state)


def reset_receipt_review(trip_dir: Path, source_file: str) -> dict:
    state = require_current_state(trip_dir)
    receipt = find_receipt(state, source_file)
    extracted = receipt.get("extracted") if isinstance(receipt.get("extracted"), dict) else {}
    receipt.update(extracted)
    receipt["field_overrides"] = {}
    extracted_type = str(extracted.get("expense_type") or "other")
    receipt["non_meal_expense_type"] = (
        extracted_type if not extracted_type.startswith("meal") else "other"
    )
    receipt["paid_by"] = receipt.get("auto_paid_by", "traveller_personal")
    receipt["paid_by_overridden"] = False
    receipt["reviewed"] = False
    receipt["reviewed_at"] = None
    receipt["receipt_total"] = receipt.get("amount")
    baseline_lines = receipt.get("extracted_line_items")
    if isinstance(baseline_lines, list):
        receipt["line_items"] = [dict(item) for item in baseline_lines if isinstance(item, dict)]
    receipt["removed_line_items"] = []
    for item in receipt.get("line_items", []):
        item["description"] = item.get("extracted_description", item.get("description", ""))
        item["amount"] = item.get("extracted_amount")
        item["is_alcohol"] = bool(item.get("auto_is_alcohol"))
        item["included_in_arvine"] = bool(item.get("auto_included_in_arvine", True))
        item["included_in_ivado"] = bool(item.get("auto_included_in_ivado", True))
        item["ivado_exclusion_reason"] = (
            "alcohol" if item["is_alcohol"] and not item["included_in_ivado"] else None
        )
        synchronize_line_alias(item, state.get("mode") or "company")
        item["inclusion_overridden"] = False
        item["alcohol_overridden"] = False
        item["inclusion_note"] = ""
        item["updated_at"] = None
        item["reviewed"] = False
        item["reviewed_at"] = None
        restore_automatic_alcohol(item)
    synchronize_receipt_aliases(receipt, state.get("mode") or "company")
    recompute_receipt(receipt, state.get("mode") or "company")
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
        receipt = by_file.get(source_file_key(expense.source_file))
        if not receipt:
            continue
        selected_mode = trip_mode(trip_dir)
        selected_lines = []
        for item in receipt["line_items"]:
            selected = dict(item)
            selected["included"] = bool(
                selected.get(
                    "included_in_ivado" if selected_mode == "ivado" else "included_in_arvine",
                    selected.get("included", True),
                )
            )
            selected_lines.append(selected)
        expense.line_items = [deserialize_line_item(item) for item in selected_lines]
        apply_expense_fields(expense, receipt)
        expense.included = bool(
            receipt.get(
                "included_in_ivado" if selected_mode == "ivado" else "included_in_arvine",
                receipt.get("included", True),
            )
        )
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
    overrides = (
        receipt.get("field_overrides") if isinstance(receipt.get("field_overrides"), dict) else {}
    )
    if receipt.get("status") in {"ready", "ok", "not_applicable"}:
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


def recompute_receipt(receipt: dict, mode: str | None = None) -> None:
    mode = mode or str(receipt.get("review_mode") or "company")
    migrate_receipt_contract_fields(receipt, mode)
    items = [item for item in receipt.get("line_items", []) if isinstance(item, dict)]
    receipt["line_items"] = items
    for item in items:
        item["status"] = "ready" if item.get("reviewed") else "review"
    values = [
        float(item["amount"]) for item in items if isinstance(item.get("amount"), (int, float))
    ]
    arvine_values = [
        float(item["amount"])
        for item in items
        if item.get("included_in_arvine", True) and isinstance(item.get("amount"), (int, float))
    ]
    ivado_values = [
        float(item["amount"])
        for item in items
        if item.get("included_in_arvine", True)
        and item.get("included_in_ivado", True)
        and isinstance(item.get("amount"), (int, float))
    ]
    line_total = round(sum(values), 2)
    arvine_included_total = round(sum(arvine_values), 2)
    ivado_included_total = round(sum(ivado_values), 2)
    arvine_excluded_total = round(line_total - arvine_included_total, 2)
    ivado_excluded_total = round(arvine_included_total - ivado_included_total, 2)
    included_total = ivado_included_total if mode == "ivado" else arvine_included_total
    excluded_total = round(line_total - included_total, 2)
    receipt_total = receipt.get("amount", receipt.get("receipt_total"))
    receipt["receipt_total"] = receipt_total
    difference = (
        round(line_total - float(receipt_total), 2)
        if isinstance(receipt_total, (int, float))
        else None
    )
    is_meal = str(receipt.get("expense_type") or "").startswith("meal")
    has_gap = any(item.get("description") == "Unreconciled meal item - review" for item in items)
    reconciled = difference == 0.0
    if not reconciled or has_gap:
        automatic_status = "review"
    elif not is_meal:
        automatic_status = "not_applicable"
    else:
        automatic_status = "ok"
    has_exclusions = excluded_total > 0.005
    blocking = bool(is_meal and has_exclusions and (not reconciled or has_gap))
    field_issues = expense_field_issues(receipt)
    all_lines_reviewed = all(bool(item.get("reviewed")) for item in items)
    ready = (
        bool(receipt.get("reviewed")) and all_lines_reviewed and not blocking and not field_issues
    )
    needs_review = bool(field_issues or blocking or automatic_status == "review")
    status = "ready" if ready else "review" if needs_review else "ok"
    claimable_ratio = (
        round(max(0.0, min(1.0, included_total / line_total)), 8)
        if has_exclusions and reconciled and line_total > 0
        else 1.0
    )
    people = max(1, int(receipt.get("number_of_people") or 1))

    def per_person(value: float | int | None) -> float | None:
        return round(float(value) / people, 2) if isinstance(value, (int, float)) else None

    receipt.update(
        {
            "line_total": line_total,
            "included_total": included_total,
            "excluded_total": excluded_total,
            "arvine_included_total": arvine_included_total,
            "arvine_excluded_total": arvine_excluded_total,
            "ivado_included_total": ivado_included_total,
            "ivado_excluded_total": ivado_excluded_total,
            "per_person_receipt_total": per_person(receipt_total),
            "per_person_line_total": per_person(line_total),
            "per_person_arvine_included_total": per_person(arvine_included_total),
            "per_person_ivado_included_total": per_person(ivado_included_total),
            "per_person_ivado_excluded_total": per_person(ivado_excluded_total),
            "difference": difference,
            "reconciled": reconciled,
            "status": status,
            "automatic_status": automatic_status,
            "ready": ready,
            "all_lines_reviewed": all_lines_reviewed,
            "blocking": blocking,
            "claimable_ratio": claimable_ratio,
            "field_issues": field_issues,
        }
    )


def serialize_line_item(item: LineItem, line_id: str, mode: str) -> dict:
    is_alcohol = bool(item.is_alcohol) if mode == "ivado" else False
    alcohol_confidence = item.alcohol_confidence if mode == "ivado" else 0.0
    alcohol_reason = item.alcohol_reason if mode == "ivado" else ""
    alcohol_matched_term = item.alcohol_matched_term if mode == "ivado" else ""
    auto_included_in_arvine = True
    auto_included_in_ivado = not is_alcohol
    auto_included = auto_included_in_ivado if mode == "ivado" else auto_included_in_arvine
    return {
        "line_id": line_id,
        "description": item.description,
        "amount": item.amount,
        "system_type": item.line_type if item.line_type in SYSTEM_TAX_LINES else None,
        "is_alcohol": is_alcohol,
        "included": auto_included,
        "included_in_arvine": auto_included_in_arvine,
        "included_in_ivado": auto_included_in_ivado,
        "ivado_exclusion_reason": (
            "alcohol" if auto_included_in_arvine and not auto_included_in_ivado else None
        ),
        "auto_is_alcohol": is_alcohol,
        "auto_included": auto_included,
        "auto_included_in_arvine": auto_included_in_arvine,
        "auto_included_in_ivado": auto_included_in_ivado,
        "extracted_description": item.description,
        "extracted_amount": item.amount,
        "confidence": item.confidence,
        "review_note": item.review_note,
        "alcohol_confidence": alcohol_confidence,
        "alcohol_reason": alcohol_reason,
        "alcohol_matched_term": alcohol_matched_term,
        "auto_alcohol_confidence": alcohol_confidence,
        "auto_alcohol_reason": alcohol_reason,
        "auto_alcohol_matched_term": alcohol_matched_term,
        "inclusion_overridden": False,
        "alcohol_overridden": False,
        "inclusion_note": "",
        "synthetic": bool(item.synthetic),
        "updated_at": None,
        "reviewed": False,
        "reviewed_at": None,
    }


def preserve_line_decisions(automatic: dict, stored: dict) -> dict:
    if stored.get("inclusion_overridden"):
        automatic["included"] = bool(stored.get("included"))
        automatic["inclusion_overridden"] = True
        automatic["inclusion_note"] = str(stored.get("inclusion_note") or "")
    if "included_in_ivado" in stored:
        automatic["included_in_ivado"] = bool(stored.get("included_in_ivado"))
        automatic["included_in_ivado_overridden"] = bool(
            stored.get("included_in_ivado_overridden")
            or automatic["included_in_ivado"] != automatic.get("auto_included_in_ivado", True)
        )
    if "ivado_exclusion_reason" in stored:
        automatic["ivado_exclusion_reason"] = stored.get("ivado_exclusion_reason")
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
    automatic["non_meal_expense_type"] = stored.get("non_meal_expense_type", "other")
    automatic["reviewed"] = bool(stored.get("reviewed"))
    automatic["reviewed_at"] = stored.get("reviewed_at")
    normalize_line_program_decisions(automatic)
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
        or item.get("included_in_arvine_overridden")
        or item.get("included_in_ivado_overridden")
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
        "gst_hst": round(float(expense.gst_hst or 0), 2),
        "qst": round(float(expense.qst or 0), 2),
        "gst_hst_number": expense.gst_hst_number,
        "qst_number": expense.qst_number,
        "business_purpose": expense.business_purpose,
        "attendees_client": expense.attendees_client,
        "tax_documentation_status": expense.tax_documentation_status,
        "review_note": expense.review_note,
        "manual_cad_override": expense.manual_cad_override,
        "manual_cad_note": expense.manual_cad_note,
        "included": bool(expense.included),
        "included_in_arvine": True,
        "included_in_ivado": bool(expense.included),
        "ivado_exclusion_reason": None,
        "paid_by": "traveller_personal",
        "number_of_people": max(1, int(expense.number_of_people or 1)),
    }


def preserve_expense_decisions(automatic: dict, stored: dict) -> None:
    extracted = automatic["extracted"]
    old_extracted = stored.get("extracted") if isinstance(stored.get("extracted"), dict) else {}
    overrides = (
        stored.get("field_overrides") if isinstance(stored.get("field_overrides"), dict) else {}
    )
    if not overrides:
        # Migration path for review snapshots created before expense-level editing.
        for field in EXPENSE_REVIEW_FIELDS - {"reviewed"}:
            if field in stored and values_differ(
                stored.get(field), old_extracted.get(field, extracted.get(field))
            ):
                overrides[field] = stored.get(field)
    overrides = dict(overrides)
    overrides.pop("reviewed", None)
    if not stored.get("paid_by_overridden"):
        overrides.pop("paid_by", None)
    for field, value in overrides.items():
        if field in EXPENSE_REVIEW_FIELDS:
            automatic[field] = normalize_paid_by(value) if field == "paid_by" else value
    stored_paid_by = normalize_paid_by(stored.get("paid_by"))
    if stored.get("paid_by_overridden") and stored_paid_by in PAID_BY_VALUES:
        automatic["paid_by"] = stored_paid_by
        automatic["paid_by_overridden"] = True
    automatic["field_overrides"] = overrides
    automatic["removed_line_items"] = [
        dict(item) for item in stored.get("removed_line_items", []) if isinstance(item, dict)
    ]
    automatic["updated_at"] = stored.get("updated_at")
    automatic["reviewed"] = bool(stored.get("reviewed"))
    automatic["reviewed_at"] = stored.get("reviewed_at")


def validate_expense_fields(fields: dict, current: dict) -> dict:
    errors: dict[str, str] = {}
    normalized: dict = {}
    for field, value in fields.items():
        if field in EXPENSE_TEXT_FIELDS:
            normalized[field] = " ".join(str(value or "").split())[:500]
        elif field in EXPENSE_NUMERIC_FIELDS:
            if value in ("", None):
                normalized[field] = 0.0 if field in SYSTEM_TAX_LINES else None
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
        elif field == "included" or field in {"included_in_arvine", "included_in_ivado"}:
            if not isinstance(value, bool):
                errors[field] = "Included must be true or false."
            else:
                normalized[field] = value
        elif field == "ivado_exclusion_reason":
            reason = str(value or "").strip() or None
            if reason not in IVADO_EXCLUSION_REASONS | {None}:
                errors[field] = "Choose a valid IVADO exclusion reason."
            else:
                normalized[field] = reason
        elif field == "paid_by":
            paid_by = normalize_paid_by(value)
            if paid_by not in PAID_BY_VALUES:
                errors[field] = "Choose traveller personal funds or company card."
            else:
                normalized[field] = paid_by
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
        elif field == "reviewed":
            if not isinstance(value, bool):
                errors[field] = "Reviewed must be true or false."
            else:
                normalized[field] = value
    if "currency" in normalized:
        normalized["currency"] = normalized["currency"].upper()
        if normalized["currency"] and normalized["currency"] not in CURRENCY_CODES:
            errors["currency"] = "Choose a currency from the ISO currency list."
    subtotal_changed = "subtotal" in normalized and values_differ(
        normalized["subtotal"], current.get("subtotal")
    )
    amount_changed = "amount" in normalized and values_differ(
        normalized["amount"], current.get("amount")
    )
    if subtotal_changed and not amount_changed and normalized["subtotal"] is not None:
        taxes = sum(normalized.get(field, current.get(field)) or 0 for field in ("gst_hst", "qst"))
        normalized["amount"] = round(normalized["subtotal"] + taxes, 2)
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


def persist_expense_to_reconciliation(
    trip_dir: Path,
    receipt: dict,
    changed_fields: set[str],
) -> None:
    try:
        from nlp_expenses.reconciliation import update_reconciliation_expense_snapshot

        update_reconciliation_expense_snapshot(trip_dir, receipt, changed_fields)
    except Exception:
        # The receipt review remains valid even if an old reconciliation file is unreadable.
        return


def refresh_reconciliation_after_receipt_scan(trip_dir: Path, receipts: list[dict]) -> None:
    try:
        from nlp_expenses.reconciliation import refresh_reconciliation_receipts

        refresh_reconciliation_receipts(trip_dir, receipts)
    except Exception:
        # Receipt extraction remains usable even when no reconciliation exists yet.
        return


def restore_automatic_alcohol(item: dict) -> None:
    item["alcohol_confidence"] = item.get("auto_alcohol_confidence", 0.0)
    item["alcohol_reason"] = item.get("auto_alcohol_reason", "")
    item["alcohol_matched_term"] = item.get("auto_alcohol_matched_term", "")


def deserialize_line_item(item: dict) -> LineItem:
    return LineItem(
        description=str(item.get("description") or ""),
        amount=float(item["amount"]) if isinstance(item.get("amount"), (int, float)) else None,
        line_type=str(item.get("system_type") or "purchase"),
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
    if item.line_type in SYSTEM_TAX_LINES:
        digest = hashlib.sha256(f"{source_file}|system-tax|{item.line_type}".encode()).hexdigest()[
            :16
        ]
        return f"LI-{digest}"
    description = re.sub(r"\s+", " ", item.description.strip().casefold())
    amount = "" if item.amount is None else f"{item.amount:.2f}"
    signature = f"{description}|{amount}"
    occurrence = occurrences.get(signature, 0) + 1
    occurrences[signature] = occurrence
    digest = hashlib.sha256(f"{source_file}|{signature}|{occurrence}".encode()).hexdigest()[:16]
    return f"LI-{digest}"


def stable_receipt_id(trip_name: str, source_file: str) -> str:
    digest = hashlib.sha256(f"{trip_name}\0{source_file}".encode()).hexdigest()[:20]
    return f"RCPT-{digest}"


def synchronize_line_alias(item: dict, mode: str) -> None:
    migrate_line_contract_fields(item, mode)
    field = "included_in_ivado" if mode == "ivado" else "included_in_arvine"
    item["included"] = bool(item.get(field, True))
    item["auto_included"] = bool(
        item.get("auto_included_in_ivado" if mode == "ivado" else "auto_included_in_arvine", True)
    )


def synchronize_receipt_aliases(receipt: dict, mode: str) -> None:
    migrate_receipt_contract_fields(receipt, mode)
    field = "included_in_ivado" if mode == "ivado" else "included_in_arvine"
    receipt["included"] = bool(receipt.get(field, True))
    for item in receipt.get("line_items", []):
        if isinstance(item, dict):
            synchronize_line_alias(item, mode)


def migrate_line_contract_fields(item: dict, mode: str) -> None:
    legacy_included = bool(item.get("included", True))
    item.setdefault("reviewed", False)
    item.setdefault("reviewed_at", None)
    if mode == "company":
        item["is_alcohol"] = False
        item["auto_is_alcohol"] = False
        item["alcohol_overridden"] = False
        for field in ("alcohol_confidence", "auto_alcohol_confidence"):
            item[field] = 0.0
        for field in (
            "alcohol_reason",
            "alcohol_matched_term",
            "auto_alcohol_reason",
            "auto_alcohol_matched_term",
        ):
            item[field] = ""
    item["included_in_arvine"] = True
    if "included_in_ivado" not in item:
        item["included_in_ivado"] = (
            legacy_included if mode == "ivado" else not item.get("is_alcohol")
        )
    item["auto_included_in_arvine"] = True
    item.setdefault(
        "auto_included_in_ivado",
        bool(item.get("auto_included", True)) if mode == "ivado" else not item.get("is_alcohol"),
    )
    normalize_line_program_decisions(item)
    active_field = "included_in_ivado" if mode == "ivado" else "included_in_arvine"
    item["included"] = bool(item.get(active_field, True))
    item["auto_included"] = bool(
        item.get(
            "auto_included_in_ivado" if mode == "ivado" else "auto_included_in_arvine",
            True,
        )
    )


def migrate_receipt_contract_fields(receipt: dict, mode: str) -> None:
    legacy_included = bool(receipt.get("included", True))
    receipt.setdefault("review_mode", mode)
    receipt.setdefault(
        "receipt_id", stable_receipt_id("legacy", str(receipt.get("source_file") or "receipt"))
    )
    receipt["included_in_arvine"] = True
    receipt.setdefault(
        "included_in_ivado", legacy_included if mode == "ivado" else receipt["included_in_arvine"]
    )
    if receipt["included_in_ivado"]:
        receipt["ivado_exclusion_reason"] = None
    elif not receipt.get("ivado_exclusion_reason"):
        receipt["ivado_exclusion_reason"] = "other"
    receipt["paid_by"] = normalize_paid_by(receipt.get("paid_by") or "traveller_personal")
    receipt["auto_paid_by"] = normalize_paid_by(receipt.get("auto_paid_by") or "traveller_personal")
    receipt.setdefault("paid_by_overridden", False)
    receipt.setdefault("reviewed", False)
    receipt.setdefault("reviewed_at", None)
    ensure_receipt_tax_lines(receipt)
    for item in receipt.get("line_items", []):
        if isinstance(item, dict):
            migrate_line_contract_fields(item, mode)
    receipt["included"] = bool(
        receipt.get("included_in_ivado" if mode == "ivado" else "included_in_arvine", True)
    )


def ensure_receipt_tax_lines(receipt: dict) -> None:
    """Collapse extracted tax-like rows into two protected, structured rows."""

    source_file = str(receipt.get("source_file") or "receipt")
    ordinary_lines = []
    existing_tax_lines: dict[str, dict] = {}
    for item in receipt.get("line_items", []):
        if not isinstance(item, dict):
            continue
        kind = (
            item.get("system_type")
            if item.get("system_type") in SYSTEM_TAX_LINES
            else tax_line_type(item.get("description"))
        )
        if kind:
            existing_tax_lines.setdefault(kind, item)
        else:
            ordinary_lines.append(item)

    tax_lines = []
    for kind, label in SYSTEM_TAX_LINES.items():
        existing = existing_tax_lines.get(kind, {})
        structured_amount = receipt.get(kind)
        existing_amount = existing.get("amount")
        amount = (
            structured_amount if isinstance(structured_amount, (int, float)) else existing_amount
        )
        amount = round(float(amount or 0), 2)
        receipt[kind] = amount
        previous_amount = existing.get("amount")
        line = dict(existing)
        digest = hashlib.sha256(f"{source_file}|system-tax|{kind}".encode()).hexdigest()[:16]
        line.update(
            {
                "line_id": f"LI-{digest}",
                "description": label,
                "amount": amount,
                "system_type": kind,
                "is_alcohol": False,
                "included": True,
                "included_in_arvine": True,
                "included_in_ivado": True,
                "ivado_exclusion_reason": None,
                "auto_is_alcohol": False,
                "auto_included": True,
                "auto_included_in_arvine": True,
                "auto_included_in_ivado": True,
                "extracted_description": label,
                "extracted_amount": amount,
                "confidence": 1.0,
                "review_note": "System tax line synchronized with the expense tax field.",
                "alcohol_confidence": 0.0,
                "alcohol_reason": "",
                "alcohol_matched_term": "",
                "auto_alcohol_confidence": 0.0,
                "auto_alcohol_reason": "",
                "auto_alcohol_matched_term": "",
                "inclusion_overridden": False,
                "alcohol_overridden": False,
                "inclusion_note": "",
                "synthetic": True,
                "manual": False,
                "updated_at": line.get("updated_at"),
                "reviewed": bool(line.get("reviewed")) if previous_amount == amount else False,
                "reviewed_at": line.get("reviewed_at") if previous_amount == amount else None,
            }
        )
        tax_lines.append(line)
    receipt["line_items"] = ordinary_lines + tax_lines
    ensure_non_meal_purchase_total(receipt)


def ensure_non_meal_purchase_total(receipt: dict) -> None:
    """Repair legacy non-meal scans that stored only the two canonical tax rows."""

    if str(receipt.get("expense_type") or "").startswith("meal"):
        return
    total = receipt.get("amount", receipt.get("receipt_total"))
    if not isinstance(total, (int, float)):
        return
    items = [item for item in receipt.get("line_items", []) if isinstance(item, dict)]
    item_total = round(
        sum(
            float(item["amount"]) for item in items if isinstance(item.get("amount"), (int, float))
        ),
        2,
    )
    gap = round(float(total) - item_total, 2)
    if gap <= max(0.05, abs(float(total)) * 0.03):
        return
    subtotal = receipt.get("subtotal")
    subtotal_matches = isinstance(subtotal, (int, float)) and abs(float(subtotal) - gap) <= 0.05
    source_file = str(receipt.get("source_file") or "receipt")
    description = (
        "Receipt subtotal"
        if subtotal_matches or not ordinary_purchase_lines(items)
        else ("Unreconciled receipt item - review")
    )
    digest = hashlib.sha256(f"{source_file}|non-meal-gap".encode()).hexdigest()[:16]
    items.insert(
        max(0, len(items) - len(SYSTEM_TAX_LINES)),
        {
            "line_id": f"LI-{digest}",
            "description": description,
            "amount": gap,
            "system_type": None,
            "included": True,
            "included_in_arvine": True,
            "included_in_ivado": True,
            "auto_included": True,
            "auto_included_in_arvine": True,
            "auto_included_in_ivado": True,
            "is_alcohol": False,
            "auto_is_alcohol": False,
            "confidence": 0.9 if subtotal_matches else 0.5,
            "review_note": (
                "Generated from the reviewed subtotal so this non-meal receipt reconciles."
                if subtotal_matches
                else "Generated from the receipt total less saved purchase and tax lines; review if an itemized breakdown is required."
            ),
            "extracted_description": description,
            "extracted_amount": gap,
            "synthetic": True,
            "manual": False,
            "reviewed": False,
            "reviewed_at": None,
        },
    )
    receipt["line_items"] = items


def ordinary_purchase_lines(items: list[dict]) -> list[dict]:
    return [item for item in items if not item.get("system_type")]


def normalize_line_program_decisions(item: dict) -> None:
    """Keep base-trip inclusion independent from IVADO's alcohol policy."""

    item["included_in_arvine"] = True
    item["auto_included_in_arvine"] = True
    item["included_in_arvine_overridden"] = False
    if item.get("is_alcohol"):
        item["included_in_ivado"] = False
        item["auto_included_in_ivado"] = False
        item["ivado_exclusion_reason"] = "alcohol"
        item["included_in_ivado_overridden"] = False
    elif item.get("ivado_exclusion_reason") == "alcohol":
        item["included_in_ivado"] = True
        item["ivado_exclusion_reason"] = None
        item["included_in_ivado_overridden"] = False
    elif item.get("included_in_ivado", True):
        item["ivado_exclusion_reason"] = None
    elif not item.get("ivado_exclusion_reason"):
        item["ivado_exclusion_reason"] = "other"


def line_item_input_fingerprint(trip_dir: Path) -> str:
    digest = hashlib.sha256()
    digest.update(trip_mode(trip_dir).encode("utf-8"))
    folder = trip_receipts_dir(trip_dir)
    if folder.exists():
        for path in list_receipt_files(folder):
            digest.update(relative_source_name(folder, path).encode("utf-8"))
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def receipt_source_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stored_receipt_matches_source(stored: dict, path: Path, state: dict | None) -> bool:
    if not path.is_file():
        return False
    stored_fingerprint = str(stored.get("source_fingerprint") or "")
    if stored_fingerprint:
        return stored_fingerprint == receipt_source_fingerprint(path)
    try:
        synced_at = datetime.fromisoformat(str((state or {}).get("synced_at") or "")).timestamp()
        return path.stat().st_mtime <= synced_at + 1
    except (OSError, TypeError, ValueError):
        # Legacy review snapshots did not store a per-file fingerprint. A
        # matching filename is the safest way to preserve existing user work.
        return True


def receipt_scan_status(trip_dir: Path) -> dict:
    """Describe which current receipt files still need extraction."""

    state = load_line_item_review_state(trip_dir)
    same_mode = bool(state and state.get("mode") == trip_mode(trip_dir))
    stored = {
        str(receipt.get("source_file") or ""): receipt
        for receipt in (state.get("receipts", []) if same_mode else [])
        if isinstance(receipt, dict) and receipt.get("source_file")
    }
    folder = trip_receipts_dir(trip_dir)
    receipts = []
    for path in list_receipt_files(folder):
        source_file = relative_source_name(folder, path)
        previous = stored.get(source_file)
        if previous and stored_receipt_matches_source(previous, path, state):
            status = "scanned"
        elif previous:
            status = "changed"
        else:
            status = "not_scanned"
        receipts.append(
            {
                "source_file": source_file,
                "status": status,
                "quality": previous.get("quality", state.get("quality")) if previous else None,
                "scanned_at": previous.get("scanned_at", state.get("synced_at"))
                if previous
                else None,
            }
        )
    unscanned_count = sum(1 for receipt in receipts if receipt["status"] != "scanned")
    return {
        "receipts": receipts,
        "total_count": len(receipts),
        "scanned_count": len(receipts) - unscanned_count,
        "unscanned_count": unscanned_count,
    }


def load_line_item_review_state(trip_dir: Path) -> dict | None:
    path = trip_dir / LINE_ITEM_REVIEW_FILE
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict) or state.get("version") not in {
        1,
        2,
        3,
        4,
        LINE_ITEM_REVIEW_VERSION,
    }:
        return None
    mode = str(state.get("mode") or trip_mode(trip_dir))
    if mode == "arvine":
        mode = "company"
    state["mode"] = mode
    for receipt in state.get("receipts", []):
        if isinstance(receipt, dict):
            migrate_receipt_contract_fields(receipt, mode)
    state["version"] = LINE_ITEM_REVIEW_VERSION
    repair_review_after_missing_receipts(trip_dir, state)
    return state


def repair_review_after_missing_receipts(trip_dir: Path, state: dict) -> bool:
    """Keep unchanged receipt reviews usable when source files were only removed."""

    if str(state.get("mode") or "") != trip_mode(trip_dir):
        return False
    receipts = [receipt for receipt in state.get("receipts", []) if isinstance(receipt, dict)]
    stored_sources = {
        str(receipt.get("source_file") or "") for receipt in receipts if receipt.get("source_file")
    }
    folder = trip_receipts_dir(trip_dir)
    current_paths = {
        relative_source_name(folder, path): path for path in list_receipt_files(folder)
    }
    current_sources = set(current_paths)
    if current_sources == stored_sources or not current_sources.issubset(stored_sources):
        return False

    try:
        synced_timestamp = datetime.fromisoformat(str(state.get("synced_at") or "")).timestamp()
    except (TypeError, ValueError):
        return False
    if any(path.stat().st_mtime > synced_timestamp + 1 for path in current_paths.values()):
        return False

    state["receipts"] = [
        receipt for receipt in receipts if str(receipt.get("source_file") or "") in current_sources
    ]
    state["input_fingerprint"] = line_item_input_fingerprint(trip_dir)
    return True


def persist_review_after_receipt_removal(trip_dir: Path) -> None:
    """Persist the safe missing-receipt repair after an in-app file deletion."""

    state = load_line_item_review_state(trip_dir)
    if state and state.get("input_fingerprint") == line_item_input_fingerprint(trip_dir):
        save_line_item_review_state(trip_dir, state)


def save_line_item_review_state(trip_dir: Path, state: dict) -> None:
    if state.get("mode") == "arvine":
        state["mode"] = "company"
    path = trip_dir / LINE_ITEM_REVIEW_FILE
    write_json_atomic(path, state)


def require_current_state(trip_dir: Path) -> dict:
    state = load_line_item_review_state(trip_dir)
    if not state or state.get("input_fingerprint") != line_item_input_fingerprint(trip_dir):
        raise ValueError(
            "Receipt files changed after the last line-item scan. Scan again before editing."
        )
    return state


def find_receipt(state: dict, source_file: str) -> dict:
    source_file = validate_source_name(source_file)
    receipt = next(
        (value for value in state.get("receipts", []) if value.get("source_file") == source_file),
        None,
    )
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
    copied.setdefault("gst_hst", 0.0)
    copied.setdefault("qst", 0.0)
    copied.setdefault("gst_hst_number", "")
    copied.setdefault("qst_number", "")
    copied.setdefault("business_purpose", "")
    copied.setdefault("attendees_client", "")
    copied.setdefault("tax_documentation_status", "")
    copied.setdefault("included", True)
    copied.setdefault("included_in_arvine", copied.get("included", True))
    copied.setdefault("included_in_ivado", copied.get("included", True))
    copied.setdefault("ivado_exclusion_reason", None)
    copied["paid_by"] = normalize_paid_by(copied.get("paid_by") or "traveller_personal")
    copied["auto_paid_by"] = normalize_paid_by(copied.get("auto_paid_by") or "traveller_personal")
    copied.setdefault("number_of_people", 1)
    copied.setdefault("manual_cad_override", None)
    copied.setdefault("manual_cad_note", "")
    copied["line_items"] = [
        dict(item) for item in receipt.get("line_items", []) if isinstance(item, dict)
    ]
    extracted = dict(receipt.get("extracted", {}))
    for field in EXPENSE_REVIEW_FIELDS:
        extracted.setdefault(field, copied.get(field))
    copied["extracted"] = extracted
    copied["field_overrides"] = dict(receipt.get("field_overrides", {}))
    copied["overridden_fields"] = sorted(copied["field_overrides"])
    expense_type = str(copied.get("expense_type") or "other")
    extracted_type = str(extracted.get("expense_type") or "other")
    copied["is_meal"] = expense_type.startswith("meal")
    copied["non_meal_expense_type"] = next(
        (
            value
            for value in (
                str(copied.get("non_meal_expense_type") or ""),
                expense_type,
                extracted_type,
                "other",
            )
            if value and not value.startswith("meal")
        ),
        "other",
    )
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
            "arvine_excluded_count": 0,
            "excluded_expense_count": 0,
            "reviewed_count": 0,
            "review_count": 0,
            "ok_count": 0,
            "ready_count": 0,
            "line_review_count": 0,
            "blocking_count": 0,
            "field_issue_count": 0,
        },
    }
