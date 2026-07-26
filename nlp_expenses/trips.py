from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

TRIP_NAME_RE = re.compile(r"^\d{6}_[A-Za-z0-9][A-Za-z0-9_-]*$")
RECEIPTS_DIR = "expenses_receipts"
STATEMENTS_DIR = "card_statements"
TRIP_CONFIG = ".nlp-expenses.json"
TRIP_MODES = {"ivado", "arvine"}


def validate_trip_name(name: str) -> bool:
    return bool(TRIP_NAME_RE.match(name))


def ensure_trip(root: Path, name: str, mode: str = "ivado") -> Path:
    if not validate_trip_name(name):
        raise ValueError("Trip folder must be named like YYYYMM_tripName, e.g. 202606_melbourne.")
    if mode not in TRIP_MODES:
        raise ValueError(f"Unknown trip mode: {mode}.")
    trip_dir = root / "trips" / name
    (trip_dir / RECEIPTS_DIR).mkdir(parents=True, exist_ok=True)
    (trip_dir / STATEMENTS_DIR).mkdir(parents=True, exist_ok=True)
    data = load_trip_config(trip_dir)
    data["mode"] = mode
    if "accounting_profile" not in data:
        from nlp_expenses.accounting import load_default_accounting_profile

        data["accounting_profile"] = load_default_accounting_profile(root)
    save_trip_config(trip_dir, data)
    return trip_dir


def list_trips(root: Path) -> list[Path]:
    trips_root = root / "trips"
    if not trips_root.exists():
        return []
    return sorted(p for p in trips_root.iterdir() if p.is_dir() and validate_trip_name(p.name))


def trip_receipts_dir(trip_dir: Path) -> Path:
    return trip_dir / RECEIPTS_DIR


def trip_statements_dir(trip_dir: Path) -> Path:
    return trip_dir / STATEMENTS_DIR


def trip_mode(trip_dir: Path) -> str:
    data = load_trip_config(trip_dir)
    mode = str(data.get("mode", "ivado")).lower()
    return mode if mode in TRIP_MODES else "ivado"


def save_trip_mode(trip_dir: Path, mode: str) -> None:
    if mode not in TRIP_MODES:
        raise ValueError(f"Unknown trip mode: {mode}.")
    data = load_trip_config(trip_dir)
    data["mode"] = mode
    save_trip_config(trip_dir, data)


def load_trip_config(trip_dir: Path) -> dict:
    config_path = trip_dir / TRIP_CONFIG
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_trip_config(trip_dir: Path, data: dict) -> None:
    config_path = trip_dir / TRIP_CONFIG
    temporary = trip_dir / f".{TRIP_CONFIG}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, config_path)
    finally:
        temporary.unlink(missing_ok=True)
