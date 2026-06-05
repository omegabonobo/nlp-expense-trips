from __future__ import annotations

import hashlib
import re
from pathlib import Path


def supplier_slug(value: str | None) -> str:
    text = (value or "unknown").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text or "unknown")[:28]


def amount_minor(amount: float | None, currency: str | None) -> str:
    if amount is None:
        return "unknown"
    multiplier = 1 if (currency or "").upper() in {"JPY", "IDR", "KRW"} else 100
    return str(int(round(amount * multiplier)))


def stable_expense_id(
    source_file: Path,
    date: str | None,
    supplier_name: str | None,
    currency: str | None,
    amount: float | None,
    raw_text: str,
) -> str:
    canonical = "|".join(
        [
            date or "unknown-date",
            supplier_slug(supplier_name),
            (currency or "UNK").upper(),
            amount_minor(amount, currency),
            " ".join(raw_text.lower().split())[:4000],
        ]
    )
    if not raw_text.strip():
        canonical += "|" + hashlib.sha1(source_file.read_bytes()).hexdigest()
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:6].upper()
    return "EXP-{date}-{supplier}-{currency}-{amount}-{digest}".format(
        date=date or "unknown-date",
        supplier=supplier_slug(supplier_name),
        currency=(currency or "UNK").upper(),
        amount=amount_minor(amount, currency),
        digest=digest,
    )

