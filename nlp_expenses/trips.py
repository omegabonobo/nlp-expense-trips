from __future__ import annotations

import re
from pathlib import Path

TRIP_NAME_RE = re.compile(r"^\d{6}_[A-Za-z0-9][A-Za-z0-9_-]*$")
RECEIPTS_DIR = "expenses_receipts"
STATEMENTS_DIR = "card_statements"


def validate_trip_name(name: str) -> bool:
    return bool(TRIP_NAME_RE.match(name))


def ensure_trip(root: Path, name: str) -> Path:
    if not validate_trip_name(name):
        raise ValueError("Trip folder must be named like YYYYMM_tripName, e.g. 202606_melbourne.")
    trip_dir = root / "trips" / name
    (trip_dir / RECEIPTS_DIR).mkdir(parents=True, exist_ok=True)
    (trip_dir / STATEMENTS_DIR).mkdir(parents=True, exist_ok=True)
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

