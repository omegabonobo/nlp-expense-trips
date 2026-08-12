from __future__ import annotations

from datetime import date

from nlp_expenses.models import Expense
from nlp_expenses.trips import source_file_key

INVOICE_FIELD_MAP = {
    "date": "date",
    "vendor": "supplier_name",
    "description": "description",
    "expense_type": "expense_type",
    "amount": "amount",
    "currency": "currency",
    "country": "country",
    "province": "province",
    "gst_hst": "gst_hst",
    "qst": "qst",
    "gst_hst_number": "gst_hst_number",
    "qst_number": "qst_number",
    "business_purpose": "business_purpose",
    "attendees_client": "attendees_client",
}
NUMERIC_INVOICE_FIELDS = {"amount", "gst_hst", "qst"}


class InvoiceValidationError(ValueError):
    def __init__(self, fields: dict[str, str]):
        self.fields = fields
        super().__init__("Correct the highlighted invoice fields.")


def apply_invoice_overrides(expenses: list[Expense], overrides: dict[str, dict]) -> None:
    for expense in expenses:
        values = overrides.get(source_file_key(expense.source_file), {})
        for field, attribute in INVOICE_FIELD_MAP.items():
            if field in values:
                setattr(expense, attribute, values[field])


def apply_manual_cad_overrides(expenses: list[Expense], overrides: dict[str, dict]) -> None:
    for expense in expenses:
        values = overrides.get(source_file_key(expense.source_file), {})
        amount = values.get("amount")
        if isinstance(amount, (int, float)):
            expense.manual_cad_override = float(amount)
            expense.manual_cad_note = str(values.get("note", ""))


def validate_invoice_fields(fields: dict, extracted: dict) -> dict:
    errors: dict[str, str] = {}
    normalized = {field: extracted.get(field) for field in INVOICE_FIELD_MAP}
    for field, value in fields.items():
        if field not in INVOICE_FIELD_MAP:
            errors[field] = "This field cannot be edited."
            continue
        if field in NUMERIC_INVOICE_FIELDS:
            if value in ("", None):
                normalized[field] = None
            else:
                try:
                    normalized[field] = round(float(value), 2)
                except (TypeError, ValueError):
                    errors[field] = "Enter a valid number."
        else:
            normalized[field] = str(value or "").strip()

    invoice_date = normalized.get("date")
    if invoice_date:
        try:
            date.fromisoformat(str(invoice_date))
        except ValueError:
            errors["date"] = "Use a valid date in YYYY-MM-DD format."
    currency = str(normalized.get("currency") or "").upper()
    normalized["currency"] = currency
    if currency and (len(currency) != 3 or not currency.isalpha()):
        errors["currency"] = "Use a three-letter currency code such as CAD or USD."
    amount = normalized.get("amount")
    if amount is not None and amount < 0:
        errors["amount"] = "A purchase total cannot be negative."
    for field in ("gst_hst", "qst"):
        tax = normalized.get(field)
        if tax is not None and tax < 0:
            errors[field] = "Tax cannot be negative."
        elif tax is not None and amount is not None and tax > 0 and tax > amount:
            errors[field] = "Tax cannot be greater than the invoice total."
    taxes = sum(value or 0 for value in (normalized.get("gst_hst"), normalized.get("qst")))
    if amount is not None and amount >= 0 and taxes > amount:
        errors["gst_hst"] = "Combined tax cannot be greater than the invoice total."
        errors["qst"] = "Combined tax cannot be greater than the invoice total."
    if errors:
        raise InvoiceValidationError(errors)
    return normalized


def invoice_values_differ(reviewed, extracted) -> bool:
    if reviewed in (None, "") and extracted in (None, ""):
        return False
    return reviewed != extracted


def validate_manual_cad(values: dict) -> tuple[float, str]:
    errors: dict[str, str] = {}
    try:
        amount = round(float(values.get("amount")), 2)
    except (TypeError, ValueError):
        amount = 0.0
        errors["manual_cad_amount"] = "Enter a valid CAD amount."
    note = str(values.get("note", "")).strip()
    if amount <= 0:
        errors["manual_cad_amount"] = "The manual CAD amount must be greater than zero."
    if not note:
        errors["manual_cad_note"] = "Explain the source of the manual CAD amount."
    if errors:
        raise InvoiceValidationError(errors)
    return amount, note


def apply_overrides_to_snapshots(extracted: list[dict], overrides: dict[str, dict]) -> list[dict]:
    result = []
    for item in extracted:
        applied = dict(item)
        applied.update(overrides.get(str(item.get("source_file")), {}))
        result.append(applied)
    for index, item in enumerate(result, start=1):
        date_value = str(item.get("date") or "").replace("-", "") or "yyyymmdd"
        item["expense_id"] = f"{date_value}_#{index}"
    return result
