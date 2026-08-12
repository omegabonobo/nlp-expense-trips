from __future__ import annotations

import hashlib
import json
from pathlib import Path

from nlp_expenses.statement_normalizer import STATEMENT_SETTINGS_FILE
from nlp_expenses.storage import write_json_atomic
from nlp_expenses.trips import (
    list_receipt_files,
    relative_source_name,
    trip_mode,
    trip_receipts_dir,
    trip_statements_dir,
)

RECONCILIATION_FILE = ".nlp-expenses-reconciliation.json"
RECONCILIATION_VERSION = 1


def deserialize_manual_matches(state: dict | None) -> dict[str, str | None]:
    raw = state.get("manual_matches", {}) if state else {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(group_id): (str(receipt_file) if receipt_file is not None else None)
        for group_id, receipt_file in raw.items()
        if isinstance(group_id, str) and (isinstance(receipt_file, str) or receipt_file is None)
    }


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


def reconciliation_input_fingerprint(trip_dir: Path) -> str:
    digest = hashlib.sha256()
    digest.update(trip_mode(trip_dir).encode("utf-8"))
    receipts_folder = trip_receipts_dir(trip_dir)
    for path in list_receipt_files(receipts_folder):
        stat = path.stat()
        source_name = relative_source_name(receipts_folder, path)
        digest.update(
            f"{receipts_folder.name}/{source_name}|{stat.st_size}|{stat.st_mtime_ns}".encode()
        )
    statements_folder = trip_statements_dir(trip_dir)
    if statements_folder.exists():
        for path in sorted(
            item
            for item in statements_folder.iterdir()
            if item.is_file() and not item.name.startswith(".")
        ):
            stat = path.stat()
            digest.update(
                f"{statements_folder.name}/{path.name}|{stat.st_size}|{stat.st_mtime_ns}".encode()
            )
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
    write_json_atomic(trip_dir / RECONCILIATION_FILE, state)
