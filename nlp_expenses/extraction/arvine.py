from __future__ import annotations

import json
import os
import re
from pathlib import Path

from nlp_expenses.models import Expense, LineItem
from nlp_expenses.tax_lines import SYSTEM_TAX_LINES, tax_line_type

from .receipts import (
    MULTI_DOCUMENT_RECEIPT_PROMPT,
    SUPPORTED_CURRENCIES,
    append_note,
    apply_missing_date_fallback,
    heuristic_parse_receipt,
    is_multi_page_pdf,
    money_matches_in_line,
    receipt_date_candidates,
    receipt_image_inputs,
)
from .text import extract_text

CANADIAN_MARKERS = re.compile(
    r"\b(?:canada|québec|quebec|montreal|montréal|toronto|ontario|vancouver|calgary)\b", re.I
)
PROVINCE_MARKERS = {
    "QC": re.compile(r"\b(?i:québec|quebec|montreal|montréal)\b|\bQC\b"),
    "ON": re.compile(r"\b(?i:ontario|toronto|ottawa)\b|\bON\b"),
    "BC": re.compile(r"\b(?i:british columbia|vancouver|victoria)\b|\bBC\b"),
    "AB": re.compile(r"\b(?i:alberta|calgary|edmonton)\b|\bAB\b"),
}


def parse_arvine_receipt(
    path: Path,
    use_llm: bool = False,
    model: str = "gpt-5.2",
    force_llm: bool = False,
) -> Expense:
    raw_text, method = extract_text(path)
    parsed = heuristic_parse_arvine_receipt(path, raw_text)
    needs_help = arvine_quality_score(parsed) < 6.0
    multi_page = is_multi_page_pdf(path)
    if use_llm and (force_llm or multi_page or needs_help or method == "empty"):
        include_images = (
            multi_page or method == "empty" or parsed.amount is None or parsed.confidence < 0.72
        )
        llm = llm_parse_arvine_receipt(path, raw_text, parsed, model, include_images)
        if llm and (force_llm or arvine_quality_score(llm) >= arvine_quality_score(parsed)):
            preserve_reconciled_heuristic_lines(llm, parsed)
            parsed = llm
    apply_missing_date_fallback(parsed, path, raw_text)
    if method == "empty":
        parsed.review_note = append_note(
            parsed.review_note, "No extractable text/OCR output; review manually."
        )
    normalize_arvine_line_items(parsed)
    for item in parsed.line_items:
        item.included = True
    parsed.corrected_amount_in_currency = parsed.amount
    parsed.tax_documentation_status = tax_documentation_status(parsed)
    return parsed


def heuristic_parse_arvine_receipt(path: Path, raw_text: str) -> Expense:
    expense = heuristic_parse_receipt(path, raw_text)
    for item in expense.line_items:
        item.included = True
    expense.corrected_amount_in_currency = expense.amount
    expense.expense_type = simplify_expense_type(expense.expense_type)
    expense.description = default_description(expense)
    expense.gst_hst = find_tax_amount(raw_text, r"\b(?:GST|HST|TPS|TVH)\b")
    expense.qst = find_tax_amount(raw_text, r"\b(?:QST|TVQ)\b")
    expense.gst_hst_number = find_registration_number(raw_text, "gst")
    expense.qst_number = find_registration_number(raw_text, "qst")
    expense.subtotal = find_subtotal(raw_text)
    if (
        expense.subtotal is None
        and expense.amount is not None
        and (expense.gst_hst is not None or expense.qst is not None)
    ):
        expense.subtotal = round(
            expense.amount - (expense.gst_hst or 0.0) - (expense.qst or 0.0), 2
        )
    expense.country, expense.province = infer_location(raw_text, expense.currency)
    normalize_arvine_line_items(expense)
    expense.tax_documentation_status = tax_documentation_status(expense)
    return expense


def simplify_expense_type(value: str | None) -> str:
    if value and value.startswith("meal"):
        return "meal"
    if value in {"flight", "hotel", "transport", "other"}:
        return value
    return "other"


def default_description(expense: Expense) -> str:
    labels = {
        "flight": "Airfare",
        "hotel": "Accommodation",
        "transport": "Ground transportation",
        "meal": "Business meal",
        "other": "Business expense",
    }
    return labels.get(simplify_expense_type(expense.expense_type), "Business expense")


def normalize_arvine_line_items(expense: Expense) -> None:
    """Create canonical tax rows and retain purchase lines for every expense type."""

    extracted_tax_lines: dict[str, LineItem] = {}
    purchase_items = []
    for item in expense.line_items:
        kind = (
            item.line_type
            if item.line_type in SYSTEM_TAX_LINES
            else tax_line_type(item.description)
        )
        if kind:
            extracted_tax_lines.setdefault(kind, item)
        elif (
            not item.description.startswith("Receipt total")
            and item.description
            not in {"Unreconciled meal item - review", "Alcohol adjustment - manual"}
            and not (
                item.synthetic
                and item.description == "Tip"
                and item.review_note.startswith("Tip inferred from the positive gap")
            )
        ):
            purchase_items.append(item)

    items = purchase_items
    for kind, label in SYSTEM_TAX_LINES.items():
        structured_amount = getattr(expense, kind)
        extracted_amount = (
            extracted_tax_lines.get(kind).amount if extracted_tax_lines.get(kind) else None
        )
        amount = round(
            float(structured_amount if structured_amount is not None else extracted_amount or 0), 2
        )
        setattr(expense, kind, amount)
        items.append(
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
    if expense.amount is not None:
        gap = round(expense.amount - sum(item.amount or 0 for item in items), 2)
        if gap > 0.05:
            is_meal = expense.expense_type == "meal"
            items.append(
                LineItem(
                    description="Tip" if is_meal else "Receipt subtotal",
                    amount=gap,
                    included=True,
                    confidence=0.5 if is_meal else 0.75,
                    review_note=(
                        "Tip inferred from the positive gap between extracted meal lines and the final receipt total."
                        if is_meal
                        else "Generated from the receipt total less tax because no complete itemized non-meal breakdown was stored."
                    ),
                    synthetic=True,
                )
            )
    for item in items:
        item.is_alcohol = False
        item.included = True
        item.alcohol_confidence = 0.0
        item.alcohol_reason = ""
        item.alcohol_matched_term = ""
    expense.line_items = items


def preserve_reconciled_heuristic_lines(preferred: Expense, heuristic: Expense) -> None:
    """Keep a complete deterministic breakdown when OpenAI omits receipt adjustments."""

    if not receipt_lines_reconcile(heuristic) or receipt_lines_reconcile(preferred):
        return
    structured_tax = round((preferred.gst_hst or 0) + (preferred.qst or 0), 2)
    lines = []
    for item in heuristic.line_items:
        if (
            structured_tax
            and re.fullmatch(r"(?:sales\s+)?tax(?:es)?", item.description.strip(), re.I)
            and item.amount is not None
            and abs(float(item.amount) - structured_tax) <= 0.05
        ):
            continue
        lines.append(item)
    preferred.line_items = lines


def receipt_lines_reconcile(expense: Expense) -> bool:
    if expense.amount is None:
        return False
    reliable_lines = [
        item
        for item in expense.line_items
        if item.line_type not in SYSTEM_TAX_LINES and not item.synthetic and item.amount is not None
    ]
    if len(reliable_lines) < 2:
        return False
    total = sum(float(item.amount or 0) for item in expense.line_items)
    for kind in SYSTEM_TAX_LINES:
        line_tax = sum(
            float(item.amount or 0)
            for item in expense.line_items
            if item.line_type == kind or tax_line_type(item.description) == kind
        )
        total += float(getattr(expense, kind) or 0) - line_tax
    return abs(round(total - float(expense.amount), 2)) <= 0.05


def find_tax_amount(text: str, label_pattern: str) -> float | None:
    pattern = re.compile(label_pattern, re.I)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not pattern.search(line):
            continue
        if re.search(r"\b(?:no|number|registration|reg)\b", line, re.I):
            continue
        values = labeled_money_values(lines, index)
        if values:
            return round(values[-1], 2)
    return None


def find_subtotal(text: str) -> float | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not re.search(r"\bsub\s*-?\s*total\b", line, re.I):
            continue
        values = labeled_money_values(lines, index)
        if values:
            return round(values[-1], 2)
    return None


def labeled_money_values(lines: list[str], index: int) -> list[float]:
    values = [
        match.amount for match in money_matches_in_line(lines[index]) if abs(match.amount) < 100000
    ]
    if values or index + 1 >= len(lines):
        return values
    following = money_matches_in_line(lines[index + 1])
    if len(following) != 1:
        return values
    match = following[0]
    if lines[index + 1][: match.start].strip() or lines[index + 1][match.end :].strip():
        return values
    return [match.amount] if abs(match.amount) < 100000 else []


def find_registration_number(text: str, tax: str) -> str:
    compact = re.sub(r"[ .-]", "", text.upper())
    if tax == "gst":
        match = re.search(r"(?<!\d)(\d{9})(RT\d{4})(?!\d)", compact)
    else:
        match = re.search(r"(?<!\d)(\d{10})(TQ\d{4})(?!\d)", compact)
    return "".join(match.groups()) if match else ""


def infer_location(text: str, currency: str | None) -> tuple[str, str]:
    province = ""
    for code, pattern in PROVINCE_MARKERS.items():
        if pattern.search(text):
            province = code
            break
    if province or currency == "CAD" or CANADIAN_MARKERS.search(text):
        return "Canada", province
    currency_countries = {
        "AUD": "Australia",
        "USD": "United States",
        "EUR": "",
        "GBP": "United Kingdom",
        "IDR": "Indonesia",
        "VND": "Vietnam",
        "QAR": "Qatar",
        "HKD": "Hong Kong",
        "CHF": "Switzerland",
    }
    return currency_countries.get(currency or "", ""), ""


def tax_documentation_status(expense: Expense) -> str:
    if expense.country and expense.country != "Canada":
        return "not_applicable"
    warnings: list[str] = []
    total = expense.amount or 0
    # CRA and Revenu Québec invoice-information thresholds are $100 and $500.
    if total >= 100 and (expense.gst_hst or 0) > 0 and not expense.gst_hst_number:
        warnings.append("GST/HST number missing")
    if total >= 100 and (expense.qst or 0) > 0 and not expense.qst_number:
        warnings.append("QST number missing")
    if expense.currency == "CAD" and total >= 500:
        if not expense.purchaser_name:
            warnings.append("purchaser name missing")
        if not expense.payment_terms:
            warnings.append("payment terms missing")
        if not expense.description:
            warnings.append("expense description missing")
    if warnings:
        note = "Tax documentation: " + "; ".join(warnings) + "."
        if note not in expense.review_note:
            expense.review_note = append_note(expense.review_note, note)
        return "review"
    return "ok" if expense.country == "Canada" or expense.currency == "CAD" else "not_applicable"


def arvine_quality_score(expense: Expense) -> float:
    score = 0.0
    score += 1.5 if expense.date else 0.0
    score += 1.5 if expense.supplier_name and expense.supplier_name != "Unknown supplier" else 0.0
    score += 1.5 if expense.amount is not None else 0.0
    score += 1.0 if expense.currency in SUPPORTED_CURRENCIES else 0.0
    score += (
        0.5 if expense.expense_type in {"flight", "hotel", "transport", "meal", "other"} else 0.0
    )
    score += min(expense.confidence, 1.0)
    return score


def llm_parse_arvine_receipt(
    path: Path,
    raw_text: str,
    heuristic: Expense,
    model: str,
    include_images: bool,
) -> Expense | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None
    nullable_string = {"type": ["string", "null"]}
    nullable_number = {"type": ["number", "null"]}
    fields = {
        "date": nullable_string,
        "supplier_name": nullable_string,
        "description": nullable_string,
        "expense_type": nullable_string,
        "amount": nullable_number,
        "currency": nullable_string,
        "subtotal": nullable_number,
        "gst_hst": nullable_number,
        "qst": nullable_number,
        "gst_hst_number": nullable_string,
        "qst_number": nullable_string,
        "country": nullable_string,
        "province": nullable_string,
        "purchaser_name": nullable_string,
        "payment_terms": nullable_string,
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "description": {"type": "string"},
                    "amount": nullable_number,
                },
                "required": ["description", "amount"],
            },
        },
        "confidence": {"type": "number"},
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": fields,
        "required": list(fields),
    }
    document_date, filename_date = receipt_date_candidates(path, raw_text)
    content = [
        {
            "type": "input_text",
            "text": (
                f"Source filename: {path.name}\n"
                f"Heuristic date/vendor/type/total: {heuristic.date}; {heuristic.supplier_name}; "
                f"{heuristic.expense_type}; {heuristic.amount} {heuristic.currency}\n\n"
                f"Receipt-content date candidate: {document_date}\n"
                f"Filename date fallback candidate: {filename_date}\n\n"
                "Unfiltered OCR/native text:\n"
                f"{raw_text[:30000]}"
            ),
        }
    ]
    if include_images:
        content.extend(receipt_image_inputs(path))
    try:
        response = OpenAI(api_key=api_key).responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Extract one business-expense receipt for Canadian bookkeeping. "
                        "Use ISO date yyyy-mm-dd and one expense_type from flight, hotel, transport, meal, other. "
                        + MULTI_DOCUMENT_RECEIPT_PROMPT
                        + "Treat a date visible in the receipt or invoice as authoritative. Use a source-filename date "
                        "only when no reliable date is present in the receipt content, and never override a clear receipt date. "
                        "Extract clearly itemized purchase, fare, fee, and service components for every receipt type. "
                        "For meal receipts, extract purchased food and drink line items without classifying them. "
                        "For every receipt type, include promotions, discounts, coupons, rebates, and credits as negative "
                        "line-item amounts exactly as shown; never make them positive or omit them. "
                        "Do not return GST, HST, QST, TPS, TVH, or TVQ as purchased line_items; return taxes only "
                        "in the structured gst_hst and qst fields. "
                        "For non-meal receipts, exclude totals and payment rows; return an empty line_items array only "
                        "when the receipt has no reliable itemized pre-tax breakdown. "
                        "GST/HST includes GST, HST, TPS, or TVH; QST includes QST or TVQ. Copy tax registration numbers "
                        "only when visible and do not invent missing tax, location, or registration data. Use ISO currency codes. "
                        "province should be a Canadian two-letter abbreviation when known. Return only schema-valid data."
                    ),
                },
                {"role": "user", "content": content},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "arvine_receipt",
                    "strict": True,
                    "schema": schema,
                }
            },
        )
        data = json.loads(response.output_text)
    except Exception:
        return None
    expense = Expense(
        source_file=path,
        expense_id="",
        date=data.get("date"),
        supplier_name=data.get("supplier_name"),
        description=data.get("description") or default_description(heuristic),
        expense_type=simplify_expense_type(data.get("expense_type")),
        amount=data.get("amount"),
        currency=(data.get("currency") or "").upper() or None,
        corrected_amount_in_currency=data.get("amount"),
        line_items=[
            LineItem(
                description=str(item.get("description") or ""),
                amount=item.get("amount"),
                included=True,
                confidence=float(data.get("confidence") or 0.75),
                review_note=("Review OpenAI-extracted line item." if include_images else ""),
            )
            for item in data.get("line_items", [])
        ],
        raw_text=raw_text,
        confidence=float(data.get("confidence") or 0.75),
        review_note="Structured with OpenAI receipt extraction; review tax fields.",
        subtotal=data.get("subtotal"),
        gst_hst=data.get("gst_hst"),
        qst=data.get("qst"),
        gst_hst_number=data.get("gst_hst_number") or "",
        qst_number=data.get("qst_number") or "",
        country=data.get("country") or "",
        province=(data.get("province") or "").upper(),
        purchaser_name=data.get("purchaser_name") or "",
        payment_terms=data.get("payment_terms") or "",
    )
    if not expense.country:
        expense.country, inferred_province = infer_location(raw_text, expense.currency)
        expense.province = expense.province or inferred_province
    for item in expense.line_items:
        item.included = True
    expense.tax_documentation_status = tax_documentation_status(expense)
    return expense
