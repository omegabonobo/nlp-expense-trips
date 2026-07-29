from __future__ import annotations

import json
import base64
import io
import mimetypes
import os
import re
from dataclasses import dataclass
from datetime import date as current_date
from datetime import datetime
from pathlib import Path

from nlp_expenses.models import Expense, LineItem

from .alcohol import AlcoholDetection, detect_alcohol
from .text import extract_text


MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
SUPPORTED_CURRENCIES = ("AUD", "CAD", "USD", "IDR", "EUR", "GBP", "VND", "QAR", "HKD", "CHF")
CURRENCY_PATTERN = "|".join(SUPPORTED_CURRENCIES)
MONEY_RE = re.compile(
    rf"(?P<cur>{CURRENCY_PATTERN}|AU\$|CA\$|\$)?\s*(?P<amt>-?(?:\d{{1,3}}(?:,\d{{3}})+|\d+,\d{{2}}|\d+)(?:\.\d{{2}})?)",
    re.I,
)
OCR_SPLIT_MONEY_RE = re.compile(
    rf"(?P<cur>{CURRENCY_PATTERN}|AU\$|CA\$|\$)\s*(?P<head>\d{{1,3}})\s+(?P<tail>\d+\.\d{{2}})",
    re.I,
)
OCR_BROKEN_DECIMAL_RE = re.compile(
    rf"(?P<cur>{CURRENCY_PATTERN}|AU\$|CA\$|\$)\s*(?P<whole>\d+)\.\s+(?P<cents>\d{{2}})(?!\d)",
    re.I,
)
ADDITIVE_CHARGE_RE = re.compile(
    r"\b(?:gst|tax|vat|surcharge|service\s+(?:fee|charge)|gratuity|tip)\b",
    re.I,
)
INCLUDED_TAX_RE = re.compile(r"\b(?:includes?|included|incl\.?)\b", re.I)


@dataclass(frozen=True)
class MoneyMatch:
    amount: float
    currency: str | None
    start: int
    end: int
    raw: str


def parse_receipt(path: Path, use_llm: bool = False, model: str = "gpt-5.2", force_llm: bool = False) -> Expense:
    raw_text, method = extract_text(path)
    parsed = heuristic_parse_receipt(path, raw_text)
    should_use_llm = use_llm and raw_text.strip() and (force_llm or needs_llm_text_fallback(parsed))
    if should_use_llm:
        llm = llm_parse_receipt(path, raw_text, parsed, model, include_images=False)
        if llm and (force_llm or receipt_quality_score(llm) >= receipt_quality_score(parsed)):
            parsed = merge_llm_receipt(path, raw_text, llm, "OpenAI text fallback")
    if use_llm and needs_llm_vision_fallback(parsed, method):
        vision = llm_parse_receipt(path, raw_text, parsed, model, include_images=True)
        if vision and (force_llm or receipt_quality_score(vision) >= receipt_quality_score(parsed)):
            parsed = merge_llm_receipt(path, raw_text, vision, "OpenAI vision fallback")
    apply_missing_date_fallback(parsed, path, raw_text)
    if method == "empty":
        parsed.review_note = append_note(parsed.review_note, "No extractable text/OCR output; review manually.")
    return parsed


def heuristic_parse_receipt(path: Path, raw_text: str) -> Expense:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    document_date, filename_date = receipt_date_candidates(path, raw_text)
    date = document_date or filename_date
    supplier = find_supplier(lines, path)
    amount, currency = find_total(raw_text)
    expense_type = classify_expense(raw_text, supplier, path.name)
    line_items = find_line_items(lines, currency, amount, expense_type)
    line_items = ensure_minimum_line_items(line_items, amount, expense_type)
    line_items = add_reconciliation_gap_line(line_items, amount, expense_type)
    line_items = add_manual_alcohol_adjustment(line_items)
    corrected = corrected_amount(amount, line_items)
    confidence = score_confidence(date, supplier, amount, raw_text)
    note = ""
    if filename_date and not document_date:
        note = append_note(
            note,
            f"Date {filename_date} inferred from the source filename because no date was found in the receipt content; review.",
        )
        confidence = min(confidence, 0.70)
    elif document_date and filename_date and document_date != filename_date:
        note = append_note(
            note,
            f"Source filename suggests date {filename_date}, but receipt content shows {document_date}; kept the receipt date.",
        )
    if currency == "AUD" and "$" in raw_text and not re.search(r"\b(AUD|AUSTRALIAN|AUSTRALIA|MELBOURNE|VICTORIA|VIC)\b", raw_text, re.I):
        note = append_note(note, "Currency inferred as AUD from $; review if receipt is not Australian.")
        confidence = min(confidence, 0.68)
    reconciliation_note = line_item_reconciliation_note(amount, line_items, expense_type)
    if reconciliation_note:
        note = append_note(note, reconciliation_note)
    return Expense(
        source_file=path,
        expense_id="",
        date=date,
        supplier_name=supplier,
        expense_type=expense_type,
        amount=amount,
        currency=currency,
        corrected_amount_in_currency=corrected,
        line_items=line_items,
        raw_text=raw_text,
        confidence=confidence,
        review_note=note,
    )


def find_date(text: str) -> str | None:
    patterns = [
        (re.compile(r"(?<!\d)(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?!\d)"), "%Y-%m-%d"),
        (re.compile(r"(?<!\d)(\d{1,2})[-/](\d{1,2})[-/](20\d{2})(?!\d)"), "%d-%m-%Y"),
        (re.compile(rf"\b(\d{{1,2}})\s+({MONTHS})[a-z]*\.?,?\s+(20\d{{2}})\b", re.I), "%d-%b-%Y"),
        (re.compile(rf"\b({MONTHS})[a-z]*\.?\s+(\d{{1,2}}),?\s+(20\d{{2}})\b", re.I), "%b-%d-%Y"),
        (re.compile(rf"\b(\d{{1,2}})({MONTHS})[a-z]*\.?(\d{{2}}|20\d{{2}})\b", re.I), "%d%b%y"),
        (re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"), "%Y-%m-%d"),
    ]
    for regex, fmt in patterns:
        for match in regex.finditer(text):
            groups = match.groups()
            if fmt == "%Y-%m-%d" and len(groups) == 3 and len(groups[0]) == 4:
                value = f"{groups[0]}-{int(groups[1]):02d}-{int(groups[2]):02d}"
            elif fmt == "%d-%m-%Y":
                value = f"{int(groups[0]):02d}-{int(groups[1]):02d}-{groups[2]}"
            elif fmt == "%d-%b-%Y":
                value = f"{int(groups[0]):02d}-{groups[1][:3].title()}-{groups[2]}"
            elif fmt == "%b-%d-%Y":
                value = f"{groups[0][:3].title()}-{int(groups[1]):02d}-{groups[2]}"
            elif fmt == "%d%b%y":
                year = groups[2] if len(groups[2]) == 4 else f"20{groups[2]}"
                if int(year) > current_date.today().year + 1:
                    continue
                value = f"{int(groups[0]):02d}-{groups[1][:3].title()}-{year}"
                fmt = "%d-%b-%Y"
            else:
                continue
            for candidate_fmt in {fmt, "%Y-%m-%d"}:
                try:
                    return datetime.strptime(value, candidate_fmt).strftime("%Y-%m-%d")
                except ValueError:
                    continue
    return None


def find_invoice_date(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    label_patterns = [
        re.compile(r"\b(?:invoice|issue|issued|booking|purchase|transaction)\s+date\b", re.I),
        re.compile(r"\bIssuing Airline and date\b", re.I),
    ]
    for idx, line in enumerate(lines):
        if any(pattern.search(line) for pattern in label_patterns):
            same_line = find_date(line)
            if same_line:
                return same_line
            for nearby in lines[idx + 1 : idx + 4]:
                nearby_date = find_date(nearby)
                if nearby_date:
                    return nearby_date
    return None


def find_filename_date(filename: str) -> str | None:
    stem = Path(filename).stem
    candidates: list[str] = []
    year_first = re.compile(r"(?<!\d)(20\d{2})[._\-\s]?(\d{1,2})[._\-\s]?(\d{1,2})(?!\d)")
    for match in year_first.finditer(stem):
        year, month, day = (int(value) for value in match.groups())
        try:
            candidates.append(datetime(year, month, day).strftime("%Y-%m-%d"))
        except ValueError:
            continue

    ending_year_patterns = [
        re.compile(r"(?<!\d)(\d{1,2})[._\-\s](\d{1,2})[._\-\s](20\d{2})(?!\d)"),
        re.compile(r"(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)"),
    ]
    for pattern in ending_year_patterns:
        for match in pattern.finditer(stem):
            first, second, year = (int(value) for value in match.groups())
            # Accept D-M-Y or M-D-Y only when the numbers identify one
            # unambiguous calendar date. Ambiguous names require user review.
            for month, day in ((second, first), (first, second)):
                try:
                    candidates.append(datetime(year, month, day).strftime("%Y-%m-%d"))
                except ValueError:
                    continue

    textual_date = find_date(re.sub(r"[_]+", " ", stem))
    if textual_date:
        candidates.append(textual_date)
    unique_candidates = list(dict.fromkeys(candidates))
    return unique_candidates[0] if len(unique_candidates) == 1 else None


def receipt_date_candidates(path: Path, raw_text: str) -> tuple[str | None, str | None]:
    """Return receipt-content evidence first and the filename fallback second."""

    document_date = find_invoice_date(raw_text) or find_date(raw_text)
    return document_date, find_filename_date(path.name)


def apply_missing_date_fallback(expense: Expense, path: Path, raw_text: str) -> None:
    """Fill a missing structured date without overriding receipt content."""

    if expense.date:
        return
    document_date, filename_date = receipt_date_candidates(path, raw_text)
    if document_date:
        expense.date = document_date
        expense.review_note = append_note(
            expense.review_note,
            "Structured extraction returned no date; used the date found in the receipt content.",
        )
        return
    if filename_date:
        expense.date = filename_date
        expense.confidence = min(expense.confidence, 0.70)
        expense.review_note = append_note(
            expense.review_note,
            f"Date {filename_date} inferred from the source filename because no date was found in the receipt content; review.",
        )


def find_supplier(lines: list[str], path: Path) -> str:
    restaurant_supplier = find_restaurant_supplier(lines)
    if restaurant_supplier:
        return restaurant_supplier
    ignored = re.compile(r"receipt|tax invoice|invoice|abn|gst|date|amount|total|florent|gmail|reply-to|to:", re.I)
    for line in lines[:14]:
        clean = re.sub(r"\s+", " ", line).strip(" :-")
        if len(clean) >= 3 and not ignored.search(clean) and not MONEY_RE.search(clean):
            return clean[:80]
    return re.sub(r"[_-]+", " ", path.stem).strip()[:80] or "Unknown supplier"


def find_restaurant_supplier(lines: list[str]) -> str | None:
    for line in lines[:20]:
        if not re.search(r"\brestaurant\b", line, re.I):
            continue
        clean = re.sub(r"\s+", " ", line).strip(" :-")
        clean = re.sub(r"(?i)^served by\s+[A-Za-z]+\s+", "", clean)
        if "«" in clean:
            clean = clean.split("«")[-1].strip()
        clean = re.sub(r"\b\d+\b.*$", "", clean).strip(" :-")
        match = re.search(r"([A-Za-z][A-Za-z &'*-]*\bRestaurant\b)", clean, re.I)
        if match:
            return title_supplier(match.group(1))
    return None


def title_supplier(value: str) -> str:
    small_words = {"and", "of", "the"}
    words = []
    for word in value.split():
        if word.lower() in small_words:
            words.append(word.lower())
        else:
            words.append(word[:1].upper() + word[1:].lower())
    return " ".join(words)[:80]


def find_total(text: str) -> tuple[float | None, str | None]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidates: list[tuple[int, float, int, str | None]] = []
    for idx, line in enumerate(lines):
        category_total = is_category_total_line(line)
        strong_total_label = bool(
            re.search(r"\b(grand total|total amount|amount paid|balance due|total)\b", line, re.I) and not category_total
        )
        if is_amount_metadata_line(line) and not strong_total_label:
            continue
        for amount, currency in amounts_in_line(line):
            weight = 0
            has_money_marker = bool(currency or "$" in line or re.search(rf"\b({CURRENCY_PATTERN})\b", line, re.I))
            normalized_currency = normalize_currency(currency, text)
            if normalized_currency == "AUD" and amount > 5000 and not strong_total_label:
                continue
            if strong_total_label:
                weight += 50
            elif category_total:
                weight += 8
            elif re.search(r"\b(paid|fare)\b", line, re.I):
                weight += 10
            elif re.search(r"\b(?:american expr|amex|visa|mastercard|card)\b", line, re.I):
                weight += 12
            if re.search(r"\b(check|echeck|table|account|phone|telephone|street|postcode|staff|covers|cover|abn|agn|invoice no|booking reference)\b", line, re.I):
                weight -= 30
            if re.search(r"\b(fare calculation|roe|nuc)\b", line, re.I):
                weight -= 20
            if re.search(r"\b(subtotal|tax|gst|tip|gratuity|discount|change|surcharge|service fee)\b", line, re.I):
                weight -= 5
            if amount > 0 and (weight > 0 or has_money_marker):
                candidates.append((weight, amount, idx, normalized_currency))
    if not candidates:
        subtotal_fallback = subtotal_plus_additives(lines, text)
        if subtotal_fallback:
            return subtotal_fallback
        return None, None
    if not any(candidate[0] >= 50 for candidate in candidates):
        subtotal_fallback = subtotal_plus_additives(lines, text)
        if subtotal_fallback:
            return subtotal_fallback
    ranked = sorted(candidates, key=lambda item: (item[0], item[1], -item[2]), reverse=True)
    return ranked[0][1], ranked[0][3] or infer_currency(text)


def subtotal_plus_additives(lines: list[str], text: str) -> tuple[float, str | None] | None:
    subtotal: tuple[int, float, str | None] | None = None
    for idx, line in enumerate(lines):
        if re.search(r"\bsubtotal\b", line, re.I):
            amounts = amounts_in_line(line)
            if amounts:
                amount, currency = amounts[-1]
                subtotal = (idx, amount, normalize_currency(currency, text))
    if not subtotal:
        return None
    subtotal_idx, subtotal_amount, currency = subtotal
    additive_total = 0.0
    for line in lines[subtotal_idx + 1 : subtotal_idx + 8]:
        if not looks_like_payable_extra_charge(line):
            continue
        amounts = amounts_in_line(line)
        if amounts:
            additive_total += amounts[-1][0]
    return round(subtotal_amount + additive_total, 2), currency or infer_currency(text)


def is_category_total_line(line: str) -> bool:
    return bool(
        re.search(r"\b(?:food|f[o0]od|fc[o0]d|beverage|bev|drink|age|misc(?:ellaneous)?)\b.*\btotal\b", line, re.I)
        or re.search(r"^\s*(?:food sales|beverage)\b", line, re.I)
    )


def amounts_in_line(line: str) -> list[tuple[float, str | None]]:
    return [(match.amount, match.currency) for match in money_matches_in_line(line)]


def money_matches_in_line(line: str) -> list[MoneyMatch]:
    values: list[MoneyMatch] = []
    occupied: list[tuple[int, int]] = []
    for match in OCR_BROKEN_DECIMAL_RE.finditer(line):
        amount_text = f"{match.group('whole')}.{match.group('cents')}"
        try:
            amount = float(amount_text)
        except ValueError:
            continue
        values.append(MoneyMatch(amount, match.group("cur"), match.start(), match.end(), match.group(0)))
        occupied.append((match.start(), match.end()))

    for match in OCR_SPLIT_MONEY_RE.finditer(line):
        if any(match.start() >= start and match.end() <= end for start, end in occupied):
            continue
        amount_text = f"{match.group('head')}{match.group('tail')}"
        try:
            amount = float(amount_text)
        except ValueError:
            continue
        values.append(MoneyMatch(amount, match.group("cur"), match.start(), match.end(), match.group(0)))
        occupied.append((match.start(), match.end()))

    for match in MONEY_RE.finditer(line):
        if any(match.start() >= start and match.end() <= end for start, end in occupied):
            continue
        raw = match.group("amt")
        currency = match.group("cur")
        if not is_valid_money_match(line, raw, currency, match.start("amt"), match.end("amt")):
            continue
        raw_normalized = normalize_money_raw(line, raw, currency)
        try:
            values.append(MoneyMatch(float(raw_normalized), currency, match.start(), match.end(), match.group(0)))
        except ValueError:
            continue
    return values


def is_valid_money_match(line: str, raw: str, currency: str | None, start: int, end: int) -> bool:
    if currency and "." not in raw and "," not in raw and re.match(r"\s+\d{3}", line[end:]):
        return False
    if currency:
        return True
    if "." not in raw and "," not in raw:
        return False
    before = line[max(0, start - 1) : start]
    after = line[end : min(len(line), end + 1)]
    if before.isalpha() or after.isalpha():
        return False
    return True


def normalize_money_raw(line: str, raw: str, currency: str | None) -> str:
    if "," in raw and "." not in raw and re.fullmatch(r"-?\d{1,3},\d{2}", raw):
        compact = raw.replace(",", ".")
    else:
        compact = raw.replace(",", "").replace(" ", "")
    if currency and "." not in compact and re.fullmatch(r"0?\d{2}", compact) and ADDITIVE_CHARGE_RE.search(line):
        return f"0.{compact[-2:]}"
    return compact


def is_amount_metadata_line(line: str) -> bool:
    if re.search(
        r"\b(?:abn|agn|acn|merchant|phone|telephone|mobile|ticket|iata|booking|reference|invoice no|account|table|check|covers?)\b",
        line,
        re.I,
    ):
        return True
    return bool(re.search(r"(?:\d[\s-]*){8,}", line) and not re.search(r"\btotal\b", line, re.I))


def normalize_currency(token: str | None, context: str) -> str | None:
    if not token:
        return infer_currency(context)
    token = token.upper().replace("$", "")
    if token in {"AU", "AUD"}:
        return "AUD"
    if token in {"CA", "CAD"}:
        return "CAD"
    if token in SUPPORTED_CURRENCIES:
        return token
    return infer_currency(context)


def infer_currency(text: str) -> str | None:
    upper = text.upper()
    for code in SUPPORTED_CURRENCIES:
        if code in upper:
            return code
    if "AUSTRALIAN DOLLAR" in upper or "AUSTRALIA" in upper or "MELBOURNE" in upper or " VIC" in upper:
        return "AUD"
    if "$" in text:
        return "AUD"
    return None


def find_line_items(lines: list[str], fallback_currency: str | None, total_amount: float | None, expense_type: str) -> list[LineItem]:
    if not is_meal_expense(expense_type):
        return []
    items: list[LineItem] = []
    skip = re.compile(
        r"\b(total|subtotal|visa|mastercard|amex|american expr|paid|payment|balance|change|covers|table|account|check|abn|agn|address|phone|telephone|served by)\b",
        re.I,
    )
    category_summary = re.compile(r"^\s*(food sales|beverage|misc(?:ellaneous)?)\b", re.I)
    for line in lines:
        if is_tax_total_summary_line(line):
            continue
        is_additive_charge = looks_like_additive_charge(line)
        if ADDITIVE_CHARGE_RE.search(line) and not is_additive_charge:
            continue
        if is_amount_metadata_line(line) and not is_additive_charge:
            continue
        if (skip.search(line) and not is_additive_charge) or category_summary.search(line):
            continue
        if not looks_like_line_item(line):
            continue
        matches = money_matches_in_line(line)
        amounts = [(match.amount, match.currency) for match in matches]
        if not amounts:
            continue
        selected = choose_line_item_money(line, matches, is_additive_charge)
        amount = selected.amount
        if amount <= 0:
            continue
        if is_parenthesized_modifier_amount(line, selected) and not is_additive_charge:
            continue
        if total_amount is not None and amount > total_amount:
            continue
        if not is_additive_charge and amount > implausible_meal_item_threshold(total_amount):
            continue
        description = clean_line_item_description(line, selected)
        description = re.sub(r"^[|!Il1x«\s@]+", "", description).strip(" .:-\t")
        if not description or len(description) < 2 or len(re.findall(r"[A-Za-z]", description)) < 3:
            continue
        confidence = 0.75 if is_additive_charge else 0.6
        alcohol = detect_alcohol(description)
        items.append(
            LineItem(
                description=description[:120],
                amount=amount,
                is_alcohol=alcohol.is_alcohol,
                included=not alcohol.is_alcohol,
                confidence=confidence,
                alcohol_confidence=alcohol.confidence,
                alcohol_reason=alcohol.reason,
                alcohol_matched_term=alcohol.matched_term,
            )
        )
    return items


def is_parenthesized_modifier_amount(line: str, selected: MoneyMatch) -> bool:
    before = line[: selected.start].rstrip()
    after = line[selected.end :].lstrip()
    return before.endswith("(") and after.startswith(")")


def looks_like_additive_charge(line: str) -> bool:
    if not ADDITIVE_CHARGE_RE.search(line):
        return False
    if is_tax_total_summary_line(line):
        return False
    if INCLUDED_TAX_RE.search(line) and not re.search(r"\b(?:surcharge|service|tip|gratuity)\b", line, re.I):
        return False
    return True


def looks_like_payable_extra_charge(line: str) -> bool:
    return bool(re.search(r"\b(?:surcharge|service\s+(?:fee|charge)|gratuity|tip)\b", line, re.I)) and not is_tax_total_summary_line(line)


def is_tax_total_summary_line(line: str) -> bool:
    return bool(
        re.search(r"\btotal\s+(?:ex|inc|incl|including|excluding)\s+tax\b", line, re.I)
        or re.search(r"\btotal\s+(?:includes?|included)\s+gst\b", line, re.I)
        or re.search(r"^\s*-?\s*(?:gst|tax free|tax exempt)\b", line, re.I)
        or re.search(r"^\s*-?\s*(?:sales tax|tax|vat)\s+\$?\s*\d", line, re.I)
    )


def choose_line_item_money(line: str, matches: list[MoneyMatch], is_additive_charge: bool) -> MoneyMatch:
    if is_additive_charge:
        return matches[-1]
    if len(matches) == 1:
        return matches[0]
    first = matches[0]
    trailing = line[first.end :].strip()
    if len(trailing) > 25 and re.search(r"[a-z]{4,}.*[a-z]{4,}", trailing, re.I):
        return first
    return matches[-1]


def clean_line_item_description(line: str, selected: MoneyMatch) -> str:
    prefix = line[: selected.start].strip()
    suffix = line[selected.end :].strip()
    description = prefix if prefix else suffix
    description = MONEY_RE.sub("", description).strip(" .:-\t")
    description = re.sub(r"\([^)]*\)\s*$", "", description).strip(" .:-\t")
    return re.sub(r"\s+", " ", description)


def implausible_meal_item_threshold(total_amount: float | None) -> float:
    if total_amount is None:
        return 250.0
    return max(250.0, total_amount * 0.8)


def is_meal_expense(expense_type: str | None) -> bool:
    return bool(expense_type and expense_type.startswith("meal"))


def looks_like_line_item(line: str) -> bool:
    if "$" in line or re.search(rf"\b({CURRENCY_PATTERN})\b", line, re.I):
        return True
    if re.match(r"^\s*[A-Za-z][A-Za-z0-9 &'*/().,-]{2,}\s+\d+[,.]\d{2}\s*$", line):
        return True
    return bool(re.match(r"^\s*\d+\s*[x«]\s+\D+.*\d+[,.]\d{2}\s*$", line, re.I))


def line_item_reconciliation_note(amount: float | None, line_items: list[LineItem], expense_type: str | None) -> str:
    if any(item.description == "Unreconciled meal item - review" for item in line_items):
        return "Unreconciled meal item line added to balance receipt total; review description/alcohol manually."
    meaningful_items = [
        item
        for item in line_items
        if item.amount is not None and not item.description.startswith("Receipt total") and item.description != "Alcohol adjustment - manual"
    ]
    if not is_meal_expense(expense_type):
        return ""
    if amount is None:
        return "Missing receipt total; review manually."
    if not meaningful_items:
        return "No reliable meal line items extracted; review alcohol manually."
    item_total = round(sum(item.amount or 0 for item in meaningful_items), 2)
    if abs(item_total - amount) > max(0.05, amount * 0.03):
        return f"Line items total {item_total:.2f} differs from receipt total {amount:.2f}; review line items/alcohol."
    return ""


def add_reconciliation_gap_line(line_items: list[LineItem], amount: float | None, expense_type: str | None) -> list[LineItem]:
    if amount is None or not is_meal_expense(expense_type):
        return line_items
    if any(item.description.startswith("Receipt total") for item in line_items):
        return line_items
    if any(item.description == "Unreconciled meal item - review" for item in line_items):
        return line_items
    item_total = round(sum(item.amount or 0 for item in line_items if item.amount is not None), 2)
    gap = round(amount - item_total, 2)
    if gap <= max(0.05, amount * 0.03):
        return line_items
    return [
        *line_items,
        LineItem(
            description="Unreconciled meal item - review",
            amount=gap,
            is_alcohol=False,
            included=True,
            confidence=0.0,
            review_note="Generated gap line because OCR-extracted meal items did not add up to the receipt total.",
            synthetic=True,
        ),
    ]


def ensure_minimum_line_items(line_items: list[LineItem], amount: float | None, expense_type: str | None) -> list[LineItem]:
    if line_items:
        return line_items
    if amount is None:
        return [
            LineItem(
                description="Receipt total missing - review",
                amount=None,
                is_alcohol=False,
                included=True,
                confidence=0.0,
                review_note="Manual review needed: receipt total was not extracted.",
                synthetic=True,
            )
        ]
    description = "Receipt total - review meal line items" if is_meal_expense(expense_type) else "Receipt total"
    note = "Manual review needed: meal line items were not reliably extracted." if is_meal_expense(expense_type) else ""
    return [
        LineItem(
            description=description,
            amount=amount,
            is_alcohol=False,
            included=True,
            confidence=0.5,
            review_note=note,
            synthetic=True,
        )
    ]


def add_manual_alcohol_adjustment(line_items: list[LineItem]) -> list[LineItem]:
    if any(item.description == "Alcohol adjustment - manual" for item in line_items):
        return line_items
    return [
        *line_items,
        LineItem(
            description="Alcohol adjustment - manual",
            amount=0.0,
            is_alcohol=True,
            included=False,
            confidence=1.0,
            review_note="Editable placeholder for alcohol missed by OCR/LLM.",
            alcohol_confidence=1.0,
            alcohol_reason="manual alcohol adjustment placeholder",
            synthetic=True,
        ),
    ]


def corrected_amount(amount: float | None, line_items: list[LineItem]) -> float | None:
    if amount is None:
        return None
    reviewed = [item for item in line_items if item.amount is not None]
    if not reviewed:
        return amount
    included_total = sum(item.amount or 0 for item in reviewed if item.included)
    return round(max(included_total, 0), 2)


def classify_expense(text: str, supplier: str, filename: str) -> str:
    blob = f"{text} {supplier} {filename}".lower()
    if any(term in blob for term in ["airways", "airline", "flight", "e-ticket", "itinerary", "airport", "garuda", "thai airways"]):
        return "flight"
    if any(term in blob for term in ["hotel", "room", "arrival", "departure", "meridien"]):
        return "hotel"
    if "uber" in blob or "taxi" in blob or "ride" in blob:
        return "transport"
    if any(term in blob for term in ["farmers", "nigel", "reine", "la rue", "gabriel", "intermission"]):
        if any(term in blob for term in ["breakfast", "cappuccino", "benedict", "banana bread", "espresso"]):
            return "meal-breakfast"
        return "meal-dinner"
    if "breakfast" in blob:
        return "meal-breakfast"
    if "lunch" in blob:
        return "meal-lunch"
    if "dinner" in blob or "restaurant" in blob:
        return "meal-dinner"
    if any(term in blob for term in ["espresso", "cafe", "bbq", "bar", "kitchen", "burger", "beer", "restaurant", "hanks", "eat-in", "eatin", "dine in", "dining", "cappuccino", "benedict"]):
        return "meal"
    return "other"


def score_confidence(date: str | None, supplier: str | None, amount: float | None, raw_text: str) -> float:
    score = 0.15 if raw_text.strip() else 0.0
    if date:
        score += 0.25
    if supplier and supplier != "Unknown supplier":
        score += 0.2
    if amount is not None:
        score += 0.25
    if len(raw_text) > 300:
        score += 0.1
    return min(score, 0.95)


def append_note(existing: str, note: str) -> str:
    return f"{existing} {note}".strip() if existing else note


def receipt_quality_score(expense: Expense) -> float:
    score = 0.0
    if expense.date:
        score += 2.0
    if expense.supplier_name and expense.supplier_name != "Unknown supplier":
        score += 2.0
    if expense.amount is not None:
        score += 2.0
    if expense.currency:
        score += 1.0
    if is_meal_expense(expense.expense_type):
        reconciliation = line_item_reconciliation_note(expense.amount, expense.line_items, expense.expense_type)
        if not reconciliation:
            score += 2.0
        elif "No reliable" not in reconciliation and "Missing" not in reconciliation:
            score += 0.75
    return score + min(expense.confidence, 1.0)


def needs_llm_text_fallback(expense: Expense) -> bool:
    if expense.confidence < 0.72:
        return True
    if expense.date is None or expense.amount is None or not expense.supplier_name:
        return True
    if is_meal_expense(expense.expense_type) and line_item_reconciliation_note(expense.amount, expense.line_items, expense.expense_type):
        return True
    return False


def needs_llm_vision_fallback(expense: Expense, extraction_method: str) -> bool:
    if extraction_method == "empty":
        return True
    if expense.amount is None:
        return True
    if is_meal_expense(expense.expense_type):
        note = line_item_reconciliation_note(expense.amount, expense.line_items, expense.expense_type)
        if note:
            return True
    return False


def llm_parse_receipt(path: Path, raw_text: str, heuristic: Expense, model: str, include_images: bool = False) -> Expense | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "date": {"type": ["string", "null"]},
            "supplier_name": {"type": ["string", "null"]},
            "expense_type": {"type": ["string", "null"]},
            "amount": {"type": ["number", "null"]},
            "currency": {"type": ["string", "null"]},
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "description": {"type": "string"},
                        "amount": {"type": ["number", "null"]},
                        "is_alcohol": {"type": "boolean"},
                    },
                    "required": ["description", "amount", "is_alcohol"],
                },
            },
            "confidence": {"type": "number"},
        },
        "required": ["date", "supplier_name", "expense_type", "amount", "currency", "line_items", "confidence"],
    }
    try:
        client = OpenAI(api_key=api_key)
        content = build_llm_content(path, raw_text, heuristic, include_images=include_images)
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Extract a tax-included trip expense receipt. Use ISO date yyyy-mm-dd. "
                        "Use all provided context: source filename, unfiltered OCR/native text, and heuristic fields. "
                        "When images are provided, inspect the image directly and use it to repair missing or poor OCR. "
                        "Treat a date visible in the receipt or invoice as authoritative. "
                        "Use a date from the source filename only when no reliable date is present in the receipt content; "
                        "never override a clear receipt date because the filename differs. "
                        "For supplier_name, prefer the merchant/restaurant/hotel/airline name over generic words like Tax Invoice, Table Account, or a file name. "
                        "For expense_type, use values like meal-breakfast, meal-lunch, meal-dinner, hotel, flight, transport, other. "
                        "For flights, hotels, transport, and other non-meal expenses, return an empty line_items array. "
                        "For meal expenses, include only actual purchased menu/food/drink line items, not ticket numbers, phone numbers, addresses, booking references, table numbers, or payment metadata. "
                        "For meal expenses, include additive GST/tax, surcharges, service fees, gratuity, and tips when they are charged as separate line amounts. "
                        "Do not add informational tax-included lines that would double-count the total. "
                        "Use short receipt labels for line item descriptions; never copy long menu marketing copy as a line item. "
                        "For meal expenses, line_items should reconcile to the tax-included receipt total when possible; mark alcoholic beverages in line_items. "
                        "Set is_alcohol=true for all alcoholic drinks, including cocktails, spirits, beer, wine, cider, sake, liqueurs, and named drink items that are commonly cocktails. "
                        "Examples that must be alcohol include Pisco Sour, Canta, Chardonnay, IPA, lager, beer, wine, gin, rum, vodka, whisky/whiskey, tequila, mezcal, Aperol Spritz, Negroni, Martini, Margarita, Old Fashioned, and Espresso Martini. "
                        "Do not mark non-alcoholic drinks like coffee, tea, juice, soft drinks, soda, water, sparkling water, 0.0% drinks, alcohol-free drinks, mocktails, or virgin cocktails as alcohol unless the receipt clearly identifies a non-zero alcohol content. "
                        "Ginger beer and root beer are non-alcoholic unless the receipt explicitly says alcoholic; beer-battered food, cooking wine, wine vinegar, and Americano coffee are not alcoholic drink lines. "
                        "If a meal total is clear but one or more purchased lines cannot be read, add one line named 'Unreconciled meal item - review' for the exact gap instead of inventing a menu item. "
                        "Return only schema-valid data."
                    ),
                },
                {"role": "user", "content": content},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "receipt_expense",
                    "strict": True,
                    "schema": schema,
                }
            },
        )
        data = json.loads(response.output_text)
    except Exception:
        return None
    return Expense(
        source_file=Path(""),
        expense_id="",
        date=data.get("date"),
        supplier_name=data.get("supplier_name"),
        expense_type=data.get("expense_type"),
        amount=data.get("amount"),
        currency=(data.get("currency") or "").upper() or None,
        line_items=[
            line_item_from_llm(item, float(data.get("confidence") or 0.75), include_images)
            for item in data.get("line_items", [])
        ],
        raw_text=raw_text,
        confidence=float(data.get("confidence") or 0.75),
    )


def line_item_from_llm(item: dict, extraction_confidence: float, include_images: bool) -> LineItem:
    description = item.get("description", "")
    local = detect_alcohol(description)
    llm_says_alcohol = bool(item.get("is_alcohol"))
    explicit_non_alcohol = (
        not local.is_alcohol
        and local.confidence >= 0.99
        and local.reason not in {"empty description", "no alcohol evidence"}
    )
    if local.is_alcohol:
        alcohol = local
    elif llm_says_alcohol and not explicit_non_alcohol:
        alcohol = AlcoholDetection(
            is_alcohol=True,
            confidence=extraction_confidence,
            reason="OpenAI classified the line as alcohol",
        )
    else:
        alcohol = local
    return LineItem(
        description=description,
        amount=item.get("amount"),
        is_alcohol=alcohol.is_alcohol,
        included=not alcohol.is_alcohol,
        confidence=extraction_confidence,
        review_note="Review OpenAI-extracted line item." if include_images else "",
        alcohol_confidence=alcohol.confidence,
        alcohol_reason=alcohol.reason,
        alcohol_matched_term=alcohol.matched_term,
    )


def build_llm_content(path: Path, raw_text: str, heuristic: Expense, include_images: bool = False) -> list[dict]:
    document_date, filename_date = receipt_date_candidates(path, raw_text)
    text = (
        f"Source filename: {path.name}\n"
        f"Heuristic date: {heuristic.date}\n"
        f"Receipt-content date candidate: {document_date}\n"
        f"Filename date fallback candidate: {filename_date}\n"
        f"Heuristic supplier_name: {heuristic.supplier_name}\n"
        f"Heuristic expense_type: {heuristic.expense_type}\n"
        f"Heuristic amount/currency: {heuristic.amount} {heuristic.currency}\n\n"
        "Unfiltered OCR/native extracted text follows:\n"
        f"{raw_text[:30000]}"
    )
    content = [{"type": "input_text", "text": text}]
    if include_images:
        content.extend(receipt_image_inputs(path))
    return content


def receipt_image_inputs(path: Path, max_pages: int = 2) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return pdf_image_inputs(path, max_pages=max_pages)
    return file_image_inputs(path)


def pdf_image_inputs(path: Path, max_pages: int = 2) -> list[dict]:
    try:
        import fitz  # PyMuPDF
    except Exception:
        return []
    images: list[dict] = []
    try:
        doc = fitz.open(str(path))
        for page_index in range(min(len(doc), max_pages)):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            encoded = base64.b64encode(pix.tobytes("png")).decode("ascii")
            images.append({"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"})
    except Exception:
        return images
    return images


def file_image_inputs(path: Path) -> list[dict]:
    if path.suffix.lower() in {".heic", ".heif"}:
        try:
            from PIL import Image

            buffer = io.BytesIO()
            with Image.open(path) as image:
                image.convert("RGB").save(buffer, format="PNG")
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            return [{"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"}]
        except Exception:
            return []
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception:
        return []
    return [{"type": "input_image", "image_url": f"data:{mime_type};base64,{encoded}"}]


def merge_llm_receipt(path: Path, raw_text: str, llm: Expense, source: str) -> Expense:
    if not is_meal_expense(llm.expense_type):
        llm.line_items = []
    llm.line_items = ensure_minimum_line_items(llm.line_items, llm.amount, llm.expense_type)
    llm.line_items = add_reconciliation_gap_line(llm.line_items, llm.amount, llm.expense_type)
    llm.line_items = add_manual_alcohol_adjustment(llm.line_items)
    corrected = corrected_amount(llm.amount, llm.line_items)
    llm.source_file = path
    llm.raw_text = raw_text
    llm.corrected_amount_in_currency = corrected
    llm.expense_id = ""
    note = f"Structured with {source}; review line items."
    reconciliation_note = line_item_reconciliation_note(llm.amount, llm.line_items, llm.expense_type)
    if reconciliation_note:
        note = append_note(note, reconciliation_note)
    llm.review_note = note
    return llm
