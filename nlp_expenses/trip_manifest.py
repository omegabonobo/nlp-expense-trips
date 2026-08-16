from __future__ import annotations

import json
from datetime import date
from hashlib import sha256
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from nlp_expenses.accounting import trip_accounting_profile
from nlp_expenses.consolidation import consolidation_view
from nlp_expenses.storage import write_text_atomic
from nlp_expenses.trip_metadata import normalize_claim_program, normalize_paid_by

CONTRACT_VERSION = "3.0.0"
CONTRACT_FILENAME = "trip-reimbursement-manifest.v3.ndjson"
CONTRACT_SCHEMA = files("nlp_expenses.contracts").joinpath(
    "trip-reimbursement-manifest.v3.schema.json"
)
CONTROL_TOLERANCE_CAD = 0.02
V3_CLAIM_PROGRAM = {
    "company_reimbursed": "arvine_only",
    "ivado_reimbursed": "ivado_sponsored",
}
V3_PAID_BY = {
    "traveller_personal": "employee_personal",
    "company_card": "arvine_corporate_bmo",
}


def manifest_output_path(trip_dir: Path) -> Path:
    return trip_dir.resolve() / CONTRACT_FILENAME


def build_trip_manifest_records(
    root: Path,
    trip_dir: Path,
    view: dict | None = None,
) -> list[dict]:
    """Compile reviewed app state into the shared reimbursement contract."""

    root = root.resolve()
    trip_dir = trip_dir.resolve()
    view = view or consolidation_view(root, trip_dir)
    if not view.get("is_ready"):
        blocking = view.get("summary", {}).get("blocking_count", 0)
        raise ValueError(
            f"Resolve the {blocking} blocking review item(s) before building the reimbursement manifest."
        )
    current_claim_program = normalize_claim_program(view.get("claim_program"))
    claim_program = V3_CLAIM_PROGRAM.get(current_claim_program)
    if not claim_program:
        raise ValueError(
            "Choose own-company reimbursement or IVADO-reimbursed before building the manifest."
        )

    metadata = view["metadata"]
    report_id = stable_report_id(trip_dir.name)
    receipts = [
        receipt_manifest_record(
            trip_dir,
            expense,
            claim_program,
        )
        for expense in view.get("expenses", [])
    ]
    report = trip_report_manifest_record(
        root,
        trip_dir,
        report_id,
        claim_program,
        metadata,
        view,
    )
    records = [*receipts, report]
    validate_manifest_records(records)
    return records


def receipt_manifest_record(
    trip_dir: Path,
    expense: dict,
    claim_program: str,
) -> dict:
    total = money(expense.get("amount"))
    total_cad = money(expense.get("total_cad"))
    if total is None or total_cad is None:
        raise ValueError(f"{expense.get('source_file')}: receipt totals are incomplete.")
    source_file = str(expense.get("source_file") or "")
    receipt_included_in_arvine = bool(expense.get("included_in_arvine", True))
    receipt_included_in_ivado = (
        claim_program == "ivado_sponsored"
        and bool(expense.get("included_in_ivado", True))
        and receipt_included_in_arvine
    )
    line_items = [
        line_manifest_record(
            item,
            position,
            receipt_included_in_arvine,
            receipt_included_in_ivado,
        )
        for position, item in enumerate(expense.get("line_items", []), start=1)
        if isinstance(item, dict)
    ]
    record = {
        "contract_version": CONTRACT_VERSION,
        "kind": "receipt",
        "id": stable_receipt_id(trip_dir.name, source_file),
        "trip_id": trip_dir.name,
        "document_date": expense.get("date"),
        "vendor": expense.get("vendor"),
        "description": expense.get("description") or expense.get("vendor") or source_file,
        "expense_type": expense.get("expense_type") or "other",
        "source_file": source_file,
        "country": str(expense.get("country") or ""),
        "province": str(expense.get("province") or ""),
        "currency": str(expense.get("currency") or "").upper(),
        "subtotal": money(expense.get("subtotal")),
        "gst": money(expense.get("gst_hst")),
        "qst": money(expense.get("qst")),
        "total": total,
        "total_cad": total_cad,
        "paid_by": V3_PAID_BY.get(normalize_paid_by(expense.get("paid_by"))),
        "number_of_people": max(1, int(expense.get("number_of_people") or 1)),
        "included_in_arvine": receipt_included_in_arvine,
        "included_in_ivado": receipt_included_in_ivado,
        "arvine_reimbursable_cad": money(expense.get("arvine_reimbursable_cad")) or 0.0,
        "ivado_claimable_cad": money(expense.get("ivado_claimable_cad")) or 0.0,
        "ivado_excluded_cad": money(expense.get("ivado_excluded_cad")) or 0.0,
        "line_items": line_items,
        "fx_rate": decimal_rate(expense.get("fx_rate")),
        "ivado_exclusion_reason": (
            expense.get("ivado_exclusion_reason")
            if claim_program == "ivado_sponsored"
            and expense.get("included_in_arvine", True)
            and not expense.get("included_in_ivado", True)
            else None
        ),
    }
    return record


def line_manifest_record(
    item: dict,
    position: int,
    receipt_included_in_arvine: bool = True,
    receipt_included_in_ivado: bool = True,
) -> dict:
    included_in_arvine = bool(item.get("included_in_arvine", True)) and receipt_included_in_arvine
    included_in_ivado = (
        bool(item.get("included_in_ivado", True))
        and included_in_arvine
        and receipt_included_in_ivado
    )
    reason = item.get("ivado_exclusion_reason")
    if included_in_ivado:
        reason = None
    elif included_in_arvine and not reason:
        reason = "alcohol" if item.get("is_alcohol") else "other"
    line_id = str(item.get("line_id") or "").strip()
    if not line_id:
        fingerprint = "\0".join(
            (
                str(position),
                str(item.get("description") or ""),
                str(money(item.get("amount")) or 0.0),
            )
        )
        line_id = f"LINE-{sha256(fingerprint.encode('utf-8')).hexdigest()[:20]}"
    return {
        "line_id": line_id,
        "description": str(item.get("description") or ""),
        "amount": money(item.get("amount")) or 0.0,
        "is_alcohol": bool(item.get("is_alcohol")),
        "included_in_arvine": included_in_arvine,
        "included_in_ivado": included_in_ivado,
        "ivado_exclusion_reason": reason,
        "review_note": str(item.get("inclusion_note") or item.get("review_note") or ""),
    }


def trip_report_manifest_record(
    root: Path,
    trip_dir: Path,
    report_id: str,
    claim_program: str,
    metadata: dict,
    view: dict,
) -> dict:
    summary = view["summary"]
    accounting_summary = contract_accounting_summary(
        trip_accounting_profile(root, trip_dir),
        view.get("accounting", {}),
    )
    employee_total = money(summary.get("employee_reimbursement_total_cad")) or 0.0
    ivado_total = money(summary.get("ivado_claim_total_cad")) or 0.0
    receipt_dates = [
        str(expense.get("date")) for expense in view.get("expenses", []) if expense.get("date")
    ]
    report_date = (
        metadata.get("report_date")
        or metadata.get("end_date")
        or (max(receipt_dates) if receipt_dates else date.today().isoformat())
    )
    record = {
        "contract_version": CONTRACT_VERSION,
        "kind": "trip_report",
        "report_id": report_id,
        "trip_id": trip_dir.name,
        "report_date": report_date,
        "claim_program": claim_program,
        "description": metadata.get("business_purpose") or trip_dir.name,
        "traveller": metadata.get("traveller"),
        "employee_reimbursement_total_cad": employee_total,
        "corporate_paid_total_cad": money(summary.get("corporate_paid_total_cad")) or 0.0,
        "ivado_claim_total_cad": ivado_total,
        "ivado_excluded_total_cad": money(summary.get("ivado_excluded_total_cad")) or 0.0,
        "accounting_summary": accounting_summary,
    }
    return record


def contract_accounting_summary(profile: dict, accounting: dict) -> dict:
    amounts = {
        str(row.get("account")): money(row.get("amount_cad")) or 0.0
        for row in accounting.get("rows", [])
        if isinstance(row, dict)
    }
    mapping = profile["account_mapping"]
    return {
        "travel_non_meal_cad": amounts.get(mapping["non_meal"], 0.0),
        "meal_deductible_cad": amounts.get(mapping["meal_deductible"], 0.0),
        "meal_non_deductible_cad": amounts.get(mapping["meal_nondeductible"], 0.0),
        "gst_receivable_cad": amounts.get(mapping["gst_hst_receivable"], 0.0),
        "qst_receivable_cad": amounts.get(mapping["qst_receivable"], 0.0),
    }


def validate_manifest_records(records: list[dict]) -> None:
    if not records:
        raise ValueError("The reimbursement manifest cannot be empty.")
    schema = json.loads(CONTRACT_SCHEMA.read_text(encoding="utf-8"))
    errors: list[str] = []
    for index, record in enumerate(records, start=1):
        definition_name = {
            "receipt": "receipt",
            "trip_report": "tripReport",
        }.get(record.get("kind"))
        if definition_name:
            record_schema = {
                "$schema": schema["$schema"],
                "$defs": schema["$defs"],
                **schema["$defs"][definition_name],
            }
        else:
            record_schema = schema
        validator = Draft202012Validator(record_schema, format_checker=FormatChecker())
        for error in sorted(validator.iter_errors(record), key=lambda item: list(item.path)):
            location = ".".join(str(value) for value in error.path) or "record"
            errors.append(f"Line {index} {location}: {error.message}")
    if errors:
        raise ValueError("Invalid reimbursement manifest:\n" + "\n".join(errors))

    receipts = [record for record in records if record.get("kind") == "receipt"]
    reports = [record for record in records if record.get("kind") == "trip_report"]
    if len(reports) != 1:
        raise ValueError("The reimbursement manifest must contain exactly one trip report.")
    if len({record["id"] for record in receipts}) != len(receipts):
        raise ValueError("Receipt IDs must be unique within the reimbursement manifest.")
    report = reports[0]
    controls = {
        "traveller reimbursement": (
            sum(record["arvine_reimbursable_cad"] for record in receipts),
            report["employee_reimbursement_total_cad"],
        ),
        "corporate paid": (
            sum(
                (
                    record["ivado_claimable_cad"]
                    if report["claim_program"] == "ivado_sponsored"
                    else record["total_cad"]
                )
                for record in receipts
                if record["paid_by"] == "arvine_corporate_bmo"
            ),
            report["corporate_paid_total_cad"],
        ),
        "IVADO claim": (
            sum(record["ivado_claimable_cad"] for record in receipts),
            report["ivado_claim_total_cad"],
        ),
        "IVADO exclusions": (
            sum(record["ivado_excluded_cad"] for record in receipts),
            report["ivado_excluded_total_cad"],
        ),
    }
    for label, (detail_total, report_total) in controls.items():
        difference = round(detail_total - report_total, 2)
        if abs(difference) > CONTROL_TOLERANCE_CAD:
            raise ValueError(
                f"Manifest {label} detail differs from the trip report by {difference:.2f} CAD."
            )
    if report["claim_program"] == "ivado_sponsored":
        reimbursed_total = round(
            report["employee_reimbursement_total_cad"] + report["corporate_paid_total_cad"],
            2,
        )
        difference = round(reimbursed_total - report["ivado_claim_total_cad"], 2)
        if abs(difference) > CONTROL_TOLERANCE_CAD:
            raise ValueError(
                "Manifest traveller/company reimbursement differs from the IVADO claim "
                f"by {difference:.2f} CAD."
            )
        reviewed_total = round(sum(record["total_cad"] for record in receipts), 2)
        ivado_reviewed_total = round(
            report["ivado_claim_total_cad"] + report["ivado_excluded_total_cad"],
            2,
        )
        difference = round(reviewed_total - ivado_reviewed_total, 2)
        if abs(difference) > CONTROL_TOLERANCE_CAD:
            raise ValueError(
                "Manifest IVADO claim plus exclusions differ from the reviewed trip "
                f"total by {difference:.2f} CAD."
            )
    component_total = round(sum(report["accounting_summary"].values()), 2)
    difference = round(component_total - report["employee_reimbursement_total_cad"], 2)
    if abs(difference) > CONTROL_TOLERANCE_CAD:
        raise ValueError(
            f"Manifest accounting components differ from traveller reimbursement by {difference:.2f} CAD."
        )


def write_trip_manifest(
    root: Path,
    trip_dir: Path,
    view: dict | None = None,
    output_path: Path | None = None,
) -> Path:
    records = build_trip_manifest_records(root, trip_dir, view=view)
    target = (output_path or manifest_output_path(trip_dir)).resolve()
    if target.parent != trip_dir.resolve():
        raise ValueError("The reimbursement manifest must be written inside the trip folder.")
    payload = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        for record in records
    )
    write_text_atomic(target, payload)
    return target


def stable_report_id(trip_name: str) -> str:
    return f"TRIP-{trip_name.upper().replace('_', '-')}"


def stable_receipt_id(trip_name: str, source_file: str) -> str:
    digest = sha256(f"{trip_name}\0{source_file}".encode()).hexdigest()[:20]
    return f"RCPT-{digest}"


def money(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def decimal_rate(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value), 10)
    except (TypeError, ValueError):
        return None
