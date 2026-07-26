from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path

from nlp_expenses.models import Expense
from nlp_expenses.trips import load_trip_config, save_trip_config


METADATA_LIST_FIELDS = {"origins", "destinations"}
POLICY_CATEGORIES = {"flight", "hotel", "transport", "meal", "other"}


def blank_trip_metadata() -> dict:
    return {
        "traveller": "",
        "company": "",
        "start_date": "",
        "end_date": "",
        "origins": [],
        "destinations": [],
        "business_purpose": "",
        "client_project": "",
        "cost_centre": "",
        "approver": "",
        "payment_method": "",
        "policy_profile": "standard",
        "policy": {
            "receipt_required_threshold": 0.0,
            "allowed_categories": sorted(POLICY_CATEGORIES),
            "meal_limit_cad": None,
            "alcohol_treatment": "review",
            "personal_expense_treatment": "review",
            "mileage_rate_cad": None,
            "per_diem_cad": None,
            "statement_coverage_buffer_days": 0,
        },
        "policy_exceptions": {},
    }


def trip_metadata(trip_dir: Path) -> dict:
    config = load_trip_config(trip_dir)
    submitted = config.get("metadata", {})
    try:
        metadata = validate_trip_metadata(submitted)
    except ValueError:
        metadata = blank_trip_metadata()
    expected = config.get("expected_accounts", [])
    metadata["expected_accounts"] = [
        str(value) for value in expected if isinstance(value, str) and value.strip()
    ]
    return metadata


def save_trip_metadata(trip_dir: Path, submitted: dict) -> dict:
    previous = trip_metadata(trip_dir)
    metadata = validate_trip_metadata(submitted)
    config = load_trip_config(trip_dir)
    config["metadata"] = {key: value for key, value in metadata.items() if key != "expected_accounts"}
    if "expected_accounts" in submitted:
        config["expected_accounts"] = metadata["expected_accounts"]
    save_trip_config(trip_dir, config)
    invalidate_reconciliation_for_metadata_change(trip_dir, previous, metadata)
    return trip_metadata(trip_dir)


def invalidate_reconciliation_for_metadata_change(
    trip_dir: Path,
    previous: dict,
    current: dict,
) -> None:
    """Invalidate only reconciliation data affected by the changed metadata."""

    try:
        from nlp_expenses.reconciliation import load_reconciliation_state, save_reconciliation_state

        state = load_reconciliation_state(trip_dir)
    except Exception:
        state = None
    if not state:
        return
    if previous.get("business_purpose") != current.get("business_purpose"):
        state["requires_resync"] = True
    coverage_fields_changed = any(
        previous.get(field) != current.get(field)
        for field in ("start_date", "end_date", "expected_accounts")
    ) or (
        previous.get("policy", {}).get("statement_coverage_buffer_days")
        != current.get("policy", {}).get("statement_coverage_buffer_days")
    )
    if coverage_fields_changed:
        state["coverage_confirmation"] = None
    save_reconciliation_state(trip_dir, state)


def validate_trip_metadata(submitted: object) -> dict:
    if submitted is None:
        submitted = {}
    if not isinstance(submitted, dict):
        raise ValueError("Trip metadata must be an object.")
    result = blank_trip_metadata()
    for field in (
        "traveller",
        "company",
        "start_date",
        "end_date",
        "business_purpose",
        "client_project",
        "cost_centre",
        "approver",
        "payment_method",
        "policy_profile",
    ):
        if field in submitted:
            result[field] = str(submitted.get(field) or "").strip()
    for field in METADATA_LIST_FIELDS:
        values = submitted.get(field, [])
        if isinstance(values, str):
            values = values.split(",")
        if not isinstance(values, list):
            raise ValueError(f"{field} must be a list.")
        result[field] = [" ".join(str(value).split()) for value in values if str(value).strip()]
    expected = submitted.get("expected_accounts", [])
    if isinstance(expected, str):
        expected = expected.replace(",", "\n").splitlines()
    if not isinstance(expected, list):
        raise ValueError("expected_accounts must be a list.")
    result["expected_accounts"] = [
        " ".join(str(value).split()) for value in expected if str(value).strip()
    ]

    for field in ("start_date", "end_date"):
        if result[field]:
            try:
                date.fromisoformat(result[field])
            except ValueError as exc:
                raise ValueError(f"{field} must use YYYY-MM-DD.") from exc
    if result["start_date"] and result["end_date"] and result["start_date"] > result["end_date"]:
        raise ValueError("Trip end date cannot be before the start date.")

    policy = deepcopy(result["policy"])
    submitted_policy = submitted.get("policy", {})
    if submitted_policy is not None and not isinstance(submitted_policy, dict):
        raise ValueError("Trip policy must be an object.")
    policy.update(submitted_policy or {})
    allowed = policy.get("allowed_categories", [])
    if isinstance(allowed, str):
        allowed = [value.strip() for value in allowed.split(",") if value.strip()]
    if not isinstance(allowed, list) or not set(allowed) <= POLICY_CATEGORIES:
        raise ValueError("Allowed categories contain an unknown expense type.")
    policy["allowed_categories"] = sorted(set(allowed))
    for field in (
        "receipt_required_threshold",
        "meal_limit_cad",
        "mileage_rate_cad",
        "per_diem_cad",
    ):
        value = policy.get(field)
        if value in ("", None):
            policy[field] = None if field != "receipt_required_threshold" else 0.0
        else:
            try:
                policy[field] = round(float(value), 2)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field} must be a valid amount.") from exc
            if policy[field] < 0:
                raise ValueError(f"{field} cannot be negative.")
    try:
        buffer_days = int(policy.get("statement_coverage_buffer_days", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Statement coverage buffer must be a whole number of days.") from exc
    if not 0 <= buffer_days <= 60:
        raise ValueError("Statement coverage buffer must be between 0 and 60 days.")
    policy["statement_coverage_buffer_days"] = buffer_days
    for field in ("alcohol_treatment", "personal_expense_treatment"):
        value = str(policy.get(field, "review")).strip().lower()
        if value not in {"allow", "review", "prohibit"}:
            raise ValueError(f"{field} must be allow, review, or prohibit.")
        policy[field] = value
    result["policy"] = policy

    exceptions = submitted.get("policy_exceptions", {})
    if exceptions is not None and not isinstance(exceptions, dict):
        raise ValueError("Policy exceptions must be an object.")
    result["policy_exceptions"] = {
        str(key): str(value).strip()
        for key, value in (exceptions or {}).items()
        if str(key).strip() and str(value).strip()
    }
    return result


def apply_trip_metadata_defaults(trip_dir: Path, expenses: list[Expense]) -> None:
    purpose = trip_metadata(trip_dir)["business_purpose"]
    if not purpose:
        return
    for expense in expenses:
        if not expense.business_purpose:
            expense.business_purpose = purpose


def required_metadata_gaps(trip_dir: Path) -> list[str]:
    metadata = trip_metadata(trip_dir)
    labels = {
        "traveller": "traveller",
        "company": "company",
        "start_date": "trip start date",
        "end_date": "trip end date",
        "business_purpose": "business purpose",
        "approver": "approver",
        "payment_method": "payment/reimbursement method",
    }
    return [label for field, label in labels.items() if not metadata.get(field)]


def trip_policy_warnings(
    trip_dir: Path,
    expenses: list[dict],
    transactions: list[dict],
) -> list[dict]:
    metadata = trip_metadata(trip_dir)
    policy = metadata["policy"]
    exceptions = metadata["policy_exceptions"]
    warnings: list[dict] = []
    allowed = set(policy["allowed_categories"])
    for expense in expenses:
        filename = str(expense.get("source_file") or "invoice")
        expense_type = str(expense.get("expense_type") or "other")
        if expense_type not in allowed:
            warnings.append(
                policy_warning(
                    f"category:{filename}",
                    f"{filename}: category {expense_type} is not allowed by the selected policy.",
                    exceptions,
                )
            )
        meal_limit = policy.get("meal_limit_cad")
        if (
            meal_limit is not None
            and expense_type == "meal"
            and isinstance(expense.get("cad_amount_used"), (int, float))
            and abs(expense["cad_amount_used"]) > meal_limit
        ):
            warnings.append(
                policy_warning(
                    f"meal_limit:{filename}",
                    f"{filename}: meal CAD {expense['cad_amount_used']:.2f} exceeds the {meal_limit:.2f} policy limit.",
                    exceptions,
                )
            )
    if policy["personal_expense_treatment"] in {"review", "prohibit"}:
        for transaction in transactions:
            for allocation in transaction.get("allocations", []):
                if allocation.get("type") == "personal":
                    warning_id = f"personal:{transaction.get('group_id')}:{allocation.get('allocation_id')}"
                    warnings.append(
                        policy_warning(
                            warning_id,
                            (
                                f"{transaction.get('description') or transaction.get('group_id')}: "
                                f"personal allocation of {allocation.get('cad_amount')} CAD requires review."
                            ),
                            exceptions,
                        )
                    )
    return warnings


def policy_warning(warning_id: str, message: str, exceptions: dict) -> dict:
    note = str(exceptions.get(warning_id, "")).strip()
    return {
        "id": warning_id,
        "message": message,
        "exception_note": note,
        "resolved": bool(note),
    }


def save_policy_exception(trip_dir: Path, warning_id: str, note: str) -> dict:
    metadata = trip_metadata(trip_dir)
    warning_id = warning_id.strip()
    note = note.strip()
    if not warning_id:
        raise ValueError("Choose a policy warning.")
    if not note:
        metadata["policy_exceptions"].pop(warning_id, None)
    else:
        metadata["policy_exceptions"][warning_id] = note
    return save_trip_metadata(trip_dir, metadata)
