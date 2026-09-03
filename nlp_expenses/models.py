from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LineItem:
    description: str
    amount: float | None = None
    line_type: str = "purchase"
    is_alcohol: bool = False
    included: bool = True
    confidence: float = 0.0
    confidence_reason: str = ""
    review_note: str = ""
    alcohol_confidence: float = 0.0
    alcohol_reason: str = ""
    alcohol_matched_term: str = ""
    line_id: str = ""
    inclusion_overridden: bool = False
    alcohol_overridden: bool = False
    inclusion_note: str = ""
    synthetic: bool = False


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
    field_confidence: dict[str, float] = field(default_factory=dict)
    field_reasons: dict[str, str] = field(default_factory=dict)
    extractor_version: str = "receipt-v1"
    review_note: str = ""
    description: str = ""
    country: str = ""
    province: str = ""
    subtotal: float | None = None
    gst_hst: float | None = None
    qst: float | None = None
    gst_hst_number: str = ""
    qst_number: str = ""
    purchaser_name: str = ""
    payment_terms: str = ""
    business_purpose: str = ""
    attendees_client: str = ""
    tax_documentation_status: str = ""
    manual_cad_override: float | None = None
    manual_cad_note: str = ""
    line_item_review_status: str = ""
    line_item_total: float | None = None
    included_line_total: float | None = None
    excluded_line_total: float | None = None
    claimable_ratio: float = 1.0
    included: bool = True
    number_of_people: int = 1


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


@dataclass
class NormalizedTransaction:
    source_file: Path
    source_row: int
    provider: str
    transaction_group_id: str
    funding_leg_id: str
    transaction_date: str | None = None
    posted_date: str | None = None
    account_label: str = ""
    cardholder: str = ""
    description: str = ""
    category: str = ""
    transaction_type: str = "other"
    status: str = ""
    direction: str = ""
    match_eligible: bool = False
    purchase_amount: float | None = None
    purchase_currency: str | None = None
    settlement_amount: float | None = None
    settlement_currency: str | None = None
    cad_amount: float | None = None
    cad_conversion_rate: float | None = None
    cad_conversion_week_start: str | None = None
    cad_conversion_week_end: str | None = None
    cad_conversion_method: str = ""
    cad_conversion_route: str = ""
    cad_conversion_source: str = ""
    cad_conversion_source_urls: list[str] = field(default_factory=list)
    cad_completeness: str = "incomplete"
    expense_id: str = ""
    suggested_expense_id: str = ""
    match_status: str = "unmatched"
    match_confidence: float = 0.0
    match_review_reason: str = ""
    normalization_status: str = "ok"
    review_note: str = ""


@dataclass
class StatementFileReport:
    source_file: Path
    provider: str = ""
    encoding: str = ""
    delimiter: str = ""
    header_row: int = 0
    mapping_confidence: float = 0.0
    column_mapping: dict[str, int] = field(default_factory=dict)
    column_labels: dict[str, str] = field(default_factory=dict)
    mapping_options: list[dict[str, object]] = field(default_factory=list)
    mapping_required: bool = False
    sign_convention: str = ""
    sign_convention_required: bool = False
    profile_reused: bool = False
    structure_fingerprint: str = ""
    preview: list[dict[str, object]] = field(default_factory=list)
    rows_read: int = 0
    rows_normalized: int = 0
    rows_skipped: int = 0
    date_convention: str = ""
    date_convention_required: bool = False
    date_samples: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class NormalizationResult:
    transactions: list[NormalizedTransaction] = field(default_factory=list)
    files: list[StatementFileReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GenerationProgress:
    stage: str
    current: int
    total: int
    message: str
