from __future__ import annotations

import json
import os
import uuid
from hashlib import sha256
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from nlp_expenses.accounting import trip_accounting_profile
from nlp_expenses.consolidation import consolidation_view


CONTRACT_VERSION = "2.0.0"
CONTRACT_FILENAME = "trip-reimbursement-manifest.v2.ndjson"
CONTRACT_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "trip-reimbursement-manifest.v2.schema.json"
)
CONTROL_TOLERANCE_CAD = 0.02


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
    claim_program = str(view.get("claim_program") or "")
    if claim_program not in {"arvine_only", "ivado_sponsored"}:
        raise ValueError("Choose Arvine only or IVADO sponsored before building the manifest.")

    metadata = view["metadata"]
    report_id = stable_report_id(trip_dir.name)
    receipts = [
        receipt_manifest_record(
            trip_dir,
            report_id,
            claim_program,
            expense,
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
    report_id: str,
    claim_program: str,
    expense: dict,
) -> dict:
    total = money(expense.get("amount"))
    total_cad = money(expense.get("total_cad"))
    if total is None or total_cad is None:
        raise ValueError(f"{expense.get('source_file')}: receipt totals are incomplete.")
    source_file = str(expense.get("source_file") or "")
    line_items = [
        line_manifest_record(item, claim_program, position)
        for position, item in enumerate(expense.get("line_items", []), start=1)
        if isinstance(item, dict)
    ]
    record = {
        "contract_version": CONTRACT_VERSION,
        "kind": "receipt",
        "id": stable_receipt_id(trip_dir.name, source_file),
        "trip_id": trip_dir.name,
        "parent_report_id": report_id,
        "claim_program": claim_program,
        "document_date": expense.get("date"),
        "vendor": expense.get("vendor"),
        "description": expense.get("description") or expense.get("vendor") or source_file,
        "currency": str(expense.get("currency") or "").upper(),
        "subtotal": money(expense.get("subtotal")),
        "gst": money(expense.get("gst_hst")),
        "qst": money(expense.get("qst")),
        "total": total,
        "total_cad": total_cad,
        "paid_by": expense.get("paid_by"),
        "arvine_reimbursable_cad": money(expense.get("arvine_reimbursable_cad")) or 0.0,
        "ivado_claimable_cad": money(expense.get("ivado_claimable_cad")) or 0.0,
        "ivado_excluded_cad": money(expense.get("ivado_excluded_cad")) or 0.0,
        "line_items": line_items,
        "source_url": (Path("expenses_receipts") / source_file).as_posix(),
        "source_file": source_file,
        "expense_type": expense.get("expense_type") or "other",
        "business_purpose": expense.get("business_purpose") or "",
        "attendees": expense.get("attendees_client") or "",
        "gst_number": expense.get("gst_hst_number") or "",
        "qst_number": expense.get("qst_number") or "",
        "statement_total_cad": money(expense.get("cad_amount_used")),
        "fx_rate": expense.get("fx_rate"),
        "fx_basis_status": expense.get("fx_basis_status"),
        "included_in_arvine": bool(expense.get("included_in_arvine", True)),
        "included_in_ivado": bool(expense.get("included_in_ivado", True)),
        "ivado_exclusion_reason": (
            expense.get("ivado_exclusion_reason")
            if claim_program == "ivado_sponsored"
            else None
        ),
        "number_of_people": int(expense.get("number_of_people") or 1),
    }
    return record


def line_manifest_record(item: dict, claim_program: str, position: int) -> dict:
    included_in_arvine = bool(item.get("included_in_arvine", True))
    included_in_ivado = bool(item.get("included_in_ivado", True)) and included_in_arvine
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
        "included_in_ivado": included_in_ivado if claim_program == "ivado_sponsored" else False,
        "ivado_exclusion_reason": reason if claim_program == "ivado_sponsored" else None,
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
    settlement = metadata.get("settlement", {})
    legs = [
        settlement_leg(
            f"{report_id}-EMPLOYEE",
            "employee_reimbursement",
            metadata.get("company"),
            metadata.get("traveller"),
            employee_total,
            settlement.get("employee_reimbursement", {}),
        )
    ]
    sponsor = None
    if claim_program == "ivado_sponsored":
        sponsor = legal_entity(
            metadata.get("sponsor"),
            metadata.get("sponsor_identifier"),
        )
        legs.append(
            settlement_leg(
                f"{report_id}-SPONSOR",
                "sponsor_reimbursement",
                metadata.get("sponsor"),
                metadata.get("company"),
                ivado_total,
                settlement.get("sponsor_reimbursement", {}),
            )
        )
    record = {
        "contract_version": CONTRACT_VERSION,
        "kind": "trip_report",
        "report_id": report_id,
        "trip_id": trip_dir.name,
        "report_date": metadata.get("report_date") or metadata.get("end_date"),
        "claim_program": claim_program,
        "description": metadata.get("business_purpose") or trip_dir.name,
        "employee": legal_entity(
            metadata.get("traveller"),
            metadata.get("traveller_identifier"),
        ),
        "company": legal_entity(
            metadata.get("company"),
            metadata.get("company_identifier"),
        ),
        "sponsor": sponsor,
        "employee_reimbursement_total_cad": employee_total,
        "corporate_paid_total_cad": money(summary.get("corporate_paid_total_cad")) or 0.0,
        "ivado_claim_total_cad": ivado_total,
        "ivado_excluded_total_cad": money(summary.get("ivado_excluded_total_cad")) or 0.0,
        "accounting_summary": accounting_summary,
        "settlement_legs": legs,
        "source_url": f"trip/{trip_dir.name}",
        "ivado_template_version": metadata.get("ivado_template_version") or "unconfirmed",
        "ivado_claimant_instruction": metadata.get("ivado_claimant_instruction") or "",
        "ivado_claimant_confirmed": bool(metadata.get("ivado_claimant_confirmed")),
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


def settlement_leg(
    leg_id: str,
    leg_type: str,
    payer: object,
    payee: object,
    amount_cad: float,
    state: object,
) -> dict:
    state = state if isinstance(state, dict) else {}
    leg = {
        "leg_id": leg_id,
        "leg_type": leg_type,
        "payer": legal_entity(payer),
        "payee": legal_entity(payee),
        "amount_cad": amount_cad,
        "status": state.get("status") or "planned",
    }
    if state.get("payment_date"):
        leg["payment_date"] = state["payment_date"]
    if state.get("payment_reference"):
        leg["payment_reference"] = state["payment_reference"]
    return leg


def legal_entity(name: object, identifier: object = None) -> dict:
    entity = {"legal_name": " ".join(str(name or "").split())}
    if identifier:
        entity["identifier"] = " ".join(str(identifier).split())
    return entity


def validate_manifest_records(records: list[dict]) -> None:
    if not records:
        raise ValueError("The reimbursement manifest cannot be empty.")
    schema = json.loads(CONTRACT_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors: list[str] = []
    for index, record in enumerate(records, start=1):
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
        "employee reimbursement": (
            sum(record["arvine_reimbursable_cad"] for record in receipts),
            report["employee_reimbursement_total_cad"],
        ),
        "corporate paid": (
            sum(
                record["total_cad"]
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
        reviewed_total = round(
            report["employee_reimbursement_total_cad"] + report["corporate_paid_total_cad"],
            2,
        )
        ivado_total = round(
            report["ivado_claim_total_cad"] + report["ivado_excluded_total_cad"],
            2,
        )
        difference = round(reviewed_total - ivado_total, 2)
        if abs(difference) > CONTROL_TOLERANCE_CAD:
            raise ValueError(
                "Manifest IVADO claim plus exclusions differ from the reviewed trip "
                f"total by {difference:.2f} CAD."
            )
    component_total = round(sum(report["accounting_summary"].values()), 2)
    difference = round(component_total - report["employee_reimbursement_total_cad"], 2)
    if abs(difference) > CONTROL_TOLERANCE_CAD:
        raise ValueError(
            f"Manifest accounting components differ from employee reimbursement by {difference:.2f} CAD."
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
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def stable_report_id(trip_name: str) -> str:
    return f"TRIP-{trip_name.upper().replace('_', '-')}"


def stable_receipt_id(trip_name: str, source_file: str) -> str:
    digest = sha256(f"{trip_name}\0{source_file}".encode("utf-8")).hexdigest()[:20]
    return f"RCPT-{digest}"


def money(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
