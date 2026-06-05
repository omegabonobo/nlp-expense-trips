from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LineItem:
    description: str
    amount: float | None = None
    is_alcohol: bool = False
    confidence: float = 0.0
    review_note: str = ""


@dataclass
class Expense:
    source_file: Path
    expense_id: str
    date: str | None = None
    supplier_name: str | None = None
    expense_type: str | None = None
    amount: float | None = None
    currency: str | None = None
    corrected_amount_in_currency: float | None = None
    line_items: list[LineItem] = field(default_factory=list)
    raw_text: str = ""
    confidence: float = 0.0
    review_note: str = ""


@dataclass
class StatementTransaction:
    source_file: Path
    date: str | None = None
    description: str = ""
    amount_cad: float | None = None
    foreign_amount: float | None = None
    foreign_currency: str | None = None
    expense_id: str = ""
    suggested_expense_id: str = ""
    match_confidence: float = 0.0
