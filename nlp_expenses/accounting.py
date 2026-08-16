from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from pathlib import Path

from nlp_expenses.storage import write_json_atomic
from nlp_expenses.trips import load_trip_config, save_trip_config

ACCOUNTING_PROFILE_FILE = ".nlp-expenses-accounting-profile.json"
PROFILE_PERCENT_FIELDS = {
    "commercial_use_pct",
    "normal_tax_recovery_pct",
    "meal_tax_recovery_pct",
    "normal_deduction_pct",
    "meal_deduction_pct",
}
PROFILE_BOOLEAN_FIELDS = {"gst_hst_registrant", "qst_registrant"}
ACCOUNT_MAPPING_KEYS = {
    "non_meal",
    "meal_deductible",
    "meal_nondeductible",
    "gst_hst_receivable",
    "qst_receivable",
}


def builtin_accounting_profile() -> dict:
    return {
        "version": "1",
        "effective_date": date.today().isoformat(),
        "company_legal_name": "",
        "traveller_reimbursement_type": "shareholder_reimbursement",
        "gst_hst_registrant": True,
        "qst_registrant": True,
        "commercial_use_pct": 1.0,
        "normal_tax_recovery_pct": 1.0,
        "meal_tax_recovery_pct": 0.5,
        "normal_deduction_pct": 1.0,
        "meal_deduction_pct": 0.5,
        "counter_account": "Shareholder Current Account",
        "tax_calculation_method": "invoice_tax_converted_at_accounting_fx",
        "account_mapping": {
            "non_meal": "Travel – Non-meal",
            "meal_deductible": "Meals – Deductible (50%)",
            "meal_nondeductible": "Meals – Non-deductible (50%)",
            "gst_hst_receivable": "GST/HST Receivable",
            "qst_receivable": "QST Receivable",
        },
    }


def load_default_accounting_profile(root: Path) -> dict:
    path = root.resolve() / ACCOUNTING_PROFILE_FILE
    if not path.exists():
        return builtin_accounting_profile()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return validate_accounting_profile(data)
    except (OSError, json.JSONDecodeError, ValueError):
        return builtin_accounting_profile()


def save_default_accounting_profile(root: Path, profile: dict) -> dict:
    validated = validate_accounting_profile(profile)
    path = root.resolve() / ACCOUNTING_PROFILE_FILE
    write_json_atomic(path, validated)
    return validated


def trip_accounting_profile(root: Path, trip_dir: Path) -> dict:
    configured = load_trip_config(trip_dir).get("accounting_profile")
    if isinstance(configured, dict):
        try:
            return validate_accounting_profile(configured)
        except ValueError:
            pass
    return builtin_accounting_profile()


def save_trip_accounting_profile(
    root: Path,
    trip_dir: Path,
    profile: dict,
    make_default: bool = False,
) -> dict:
    validated = validate_accounting_profile(profile)
    config = load_trip_config(trip_dir)
    config["accounting_profile"] = deepcopy(validated)
    save_trip_config(trip_dir, config)
    if make_default:
        save_default_accounting_profile(root, validated)
    return validated


def validate_accounting_profile(profile: object) -> dict:
    if not isinstance(profile, dict):
        raise ValueError("Accounting profile must be an object.")
    baseline = builtin_accounting_profile()
    result = deepcopy(baseline)
    result.update({key: value for key, value in profile.items() if key != "account_mapping"})

    for field in PROFILE_PERCENT_FIELDS:
        try:
            value = float(result[field])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a number from 0 to 1.") from exc
        if not 0 <= value <= 1:
            raise ValueError(f"{field} must be between 0 and 1.")
        result[field] = round(value, 6)
    for field in PROFILE_BOOLEAN_FIELDS:
        if not isinstance(result[field], bool):
            raise ValueError(f"{field} must be true or false.")

    effective_date = str(result.get("effective_date", "")).strip()
    try:
        date.fromisoformat(effective_date)
    except ValueError as exc:
        raise ValueError("Profile effective date must use YYYY-MM-DD.") from exc
    result["effective_date"] = effective_date
    result["version"] = str(result.get("version", "")).strip() or "1"

    for field in (
        "company_legal_name",
        "traveller_reimbursement_type",
        "counter_account",
        "tax_calculation_method",
    ):
        result[field] = str(result.get(field, "")).strip()
    if not result["counter_account"]:
        raise ValueError("Counter-account is required.")

    submitted_mapping = profile.get("account_mapping", {})
    if submitted_mapping is not None and not isinstance(submitted_mapping, dict):
        raise ValueError("Account mapping must be an object.")
    mapping = deepcopy(baseline["account_mapping"])
    mapping.update(submitted_mapping or {})
    unknown = set(mapping) - ACCOUNT_MAPPING_KEYS
    if unknown:
        raise ValueError(f"Unknown account mapping: {sorted(unknown)[0]}.")
    for key in ACCOUNT_MAPPING_KEYS:
        mapping[key] = str(mapping.get(key, "")).strip()
        if not mapping[key]:
            raise ValueError(f"Account mapping {key} is required.")
    result["account_mapping"] = mapping
    return result
