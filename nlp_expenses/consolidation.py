from __future__ import annotations

import json
import os
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from nlp_expenses.accounting import trip_accounting_profile
from nlp_expenses.lifecycle import review_input_snapshot
from nlp_expenses.line_items import line_item_review_view
from nlp_expenses.reconciliation import accounting_basis, reconciliation_view
from nlp_expenses.trip_metadata import required_metadata_gaps, trip_metadata
from nlp_expenses.trips import trip_mode, trip_statements_dir


FINALIZATION_FILE = ".nlp-expenses-finalization.json"
FINALIZATION_VERSION = 1


def consolidation_view(root: Path, trip_dir: Path) -> dict:
    """Calculate the app's final claim preview from the persisted review decisions."""

    root = root.resolve()
    trip_dir = trip_dir.resolve()
    mode = trip_mode(trip_dir)
    metadata = trip_metadata(trip_dir)
    claim_program = metadata.get("claim_program") or ""
    review = line_item_review_view(trip_dir)
    reconciliation = reconciliation_view(trip_dir)
    statements_present = any(
        path.is_file() and not path.name.startswith(".")
        for path in trip_statements_dir(trip_dir).iterdir()
    )
    issues: list[dict] = []

    if not review["available"]:
        add_issue(issues, "receipt_scan", "blocking", "Scan the receipts before reviewing results.", "receipt-review")
    elif review["stale"]:
        add_issue(issues, "receipt_scan", "blocking", "Receipt files changed; rescan them.", "receipt-review")
    else:
        if review["summary"]["blocking_count"]:
            add_issue(
                issues,
                "receipt_lines",
                "blocking",
                f"{review['summary']['blocking_count']} receipt(s) have excluded lines without a reconciled total.",
                "receipt-review",
            )
        for receipt in review["receipts"]:
            for field_issue in receipt.get("field_issues", []):
                add_issue(
                    issues,
                    "expense_fields",
                    "blocking",
                    f"{receipt['source_file']}: {field_issue}.",
                    f"receipt-{receipt['source_file']}",
                    receipt["source_file"],
                )

    if statements_present:
        if not reconciliation["available"]:
            add_issue(
                issues,
                "reconciliation",
                "blocking",
                "Sync receipts and statements before finalizing.",
                "reconciliation",
            )
        elif reconciliation["stale"]:
            add_issue(
                issues,
                "reconciliation",
                "blocking",
                "Receipt or statement decisions changed; sync again.",
                "reconciliation",
            )
        else:
            for transaction in reconciliation["transactions"]:
                if not transaction.get("match_eligible") or transaction.get("ignored"):
                    continue
                if transaction_needs_review(transaction):
                    add_issue(
                        issues,
                        "statement_mapping",
                        "blocking",
                        f"{transaction.get('description') or 'Statement transaction'} needs a mapping or CAD review.",
                        "statement-mappings",
                        transaction.get("group_id"),
                    )
            for warning in reconciliation.get("policy_warnings", []):
                if not warning.get("resolved"):
                    add_issue(
                        issues,
                        "policy",
                        "blocking",
                        warning["message"],
                        "policy-review",
                        warning.get("id"),
                    )

    for gap in required_metadata_gaps(trip_dir):
        add_issue(
            issues,
            "trip_details",
            "blocking",
            f"Complete trip detail: {gap}.",
            "trip-record",
            gap,
        )
    reconciliation_expenses = {
        expense["source_file"]: expense
        for expense in reconciliation.get("expenses", [])
        if isinstance(expense, dict) and expense.get("source_file")
    }
    expenses = []
    for receipt in review.get("receipts", []):
        result = calculate_expense_result(
            receipt,
            reconciliation_expenses.get(receipt["source_file"]),
            claim_program=claim_program,
        )
        expenses.append(result)
        if result["included"] and result["cad_amount_used"] is None:
            add_issue(
                issues,
                "cad_amount",
                "blocking",
                f"{receipt['source_file']}: no exact CAD amount is available.",
                f"receipt-{receipt['source_file']}",
                receipt["source_file"],
            )
        elif result["included"] and result["fx_basis_status"] == "receipt_fallback_mismatch":
            add_issue(
                issues,
                "fx_basis",
                "warning",
                (
                    f"{receipt['source_file']}: the statement original amount does not align "
                    "with the receipt total or traveller share; FX uses the receipt total. "
                    "Verify the receipt amount, statement mapping, or enter an exact manual CAD amount."
                ),
                f"receipt-{receipt['source_file']}",
                receipt["source_file"],
            )
        if (
            claim_program == "ivado_sponsored"
            and receipt.get("included_in_arvine", True)
            and not receipt.get("included_in_ivado", True)
            and not receipt.get("ivado_exclusion_reason")
        ):
            add_issue(
                issues,
                "ivado_exclusion",
                "blocking",
                f"{receipt['source_file']}: choose a reason for the IVADO-excluded receipt.",
                f"receipt-{receipt['source_file']}",
                receipt["source_file"],
            )
        for item in receipt.get("line_items", []):
            if (
                claim_program == "ivado_sponsored"
                and
                item.get("included_in_arvine", True)
                and not item.get("included_in_ivado", True)
                and not item.get("ivado_exclusion_reason")
            ):
                add_issue(
                    issues,
                    "ivado_exclusion",
                    "blocking",
                    f"{receipt['source_file']}: choose a reason for each IVADO-excluded line.",
                    f"receipt-{receipt['source_file']}",
                    item.get("line_id"),
                )

    accounting = calculate_accounting_summary(
        expenses,
        trip_accounting_profile(root, trip_dir),
        mode,
    )
    reviewed_total = round(sum(item["total_cad"] or 0 for item in expenses), 2)
    employee_reimbursement = round(
        sum(item["arvine_reimbursable_cad"] or 0 for item in expenses),
        2,
    )
    corporate_paid = round(sum(item["corporate_paid_cad"] or 0 for item in expenses), 2)
    ivado_claim = round(sum(item["ivado_claimable_cad"] or 0 for item in expenses), 2)
    ivado_excluded = round(sum(item["ivado_excluded_cad"] or 0 for item in expenses), 2)
    payer_control = round(reviewed_total - employee_reimbursement - corporate_paid, 2)
    ivado_control = (
        round(reviewed_total - ivado_claim - ivado_excluded, 2)
        if claim_program == "ivado_sponsored"
        else 0.0
    )
    accounting_control = round(accounting["journal_total"] - employee_reimbursement, 2)
    for control_id, difference, label in (
        ("payer_control", payer_control, "Reviewed total does not equal employee plus corporate-paid amounts"),
        ("ivado_control", ivado_control, "IVADO claim plus exclusions does not equal the reviewed total"),
        ("accounting_control", accounting_control, "Accounting components do not equal employee reimbursement"),
    ):
        if abs(difference) > 0.02:
            add_issue(
                issues,
                control_id,
                "blocking",
                f"{label}; difference {difference:.2f} CAD.",
                "contract-controls",
            )
    blocking_count = sum(issue["severity"] == "blocking" for issue in issues)
    warnings = sum(issue["severity"] == "warning" for issue in issues)
    finalization = finalization_view(root, trip_dir)
    return {
        "available": review["available"],
        "mode": mode,
        "claim_program": claim_program,
        "metadata": metadata,
        "is_ready": review["available"] and blocking_count == 0,
        "is_finalized": finalization["current"],
        "finalized_at": finalization.get("finalized_at"),
        "issues": issues,
        "expenses": expenses,
        "accounting": accounting,
        "controls": {
            "payer_difference_cad": payer_control,
            "ivado_difference_cad": ivado_control,
            "accounting_difference_cad": accounting_control,
            "tolerance_cad": 0.02,
            "valid": all(
                abs(value) <= 0.02
                for value in (payer_control, ivado_control, accounting_control)
            ),
        },
        "summary": {
            "expense_count": len(expenses),
            "included_expense_count": sum(item["included"] for item in expenses),
            "excluded_expense_count": sum(not item["included"] for item in expenses),
            "matched_expense_count": sum(item["cad_source"] in {"statement", "statement_aggregated", "allocation", "allocation_aggregated"} for item in expenses),
            "claimable_cad": employee_reimbursement,
            "reviewed_total_cad": reviewed_total,
            "employee_reimbursement_total_cad": employee_reimbursement,
            "corporate_paid_total_cad": corporate_paid,
            "ivado_claim_total_cad": ivado_claim,
            "ivado_excluded_total_cad": ivado_excluded,
            "excluded_or_personal_cad": round(
                sum(item.get("arvine_excluded_cad") or 0 for item in expenses),
                2,
            ),
            "blocking_count": blocking_count,
            "warning_count": warnings,
        },
    }


def calculate_expense_result(
    receipt: dict,
    reconciled: dict | None,
    claim_program: str | None = None,
) -> dict:
    reconciled = reconciled or {}
    included_in_arvine = bool(
        receipt.get("included_in_arvine", receipt.get("included", True))
    )
    included_in_ivado = bool(
        receipt.get("included_in_ivado", receipt.get("included", True))
    ) and included_in_arvine
    paid_by = str(receipt.get("paid_by") or "employee_personal")
    people = max(1, int(receipt.get("number_of_people") or 1))
    amount = numeric(receipt.get("amount"))
    line_total = numeric(receipt.get("line_total"))
    arvine_included_line_total = numeric(
        receipt.get("arvine_included_total", receipt.get("included_total"))
    )
    ivado_included_line_total = numeric(
        receipt.get("ivado_included_total", receipt.get("included_total"))
    )
    arvine_excluded_line_total = numeric(receipt.get("arvine_excluded_total"))
    if arvine_excluded_line_total is None:
        arvine_excluded_line_total = (
            round((line_total or 0) - (arvine_included_line_total or 0), 2)
            if line_total is not None
            else 0.0
        )
    ivado_excluded_line_total = numeric(receipt.get("ivado_excluded_total"))
    if ivado_excluded_line_total is None:
        ivado_excluded_line_total = (
            round((arvine_included_line_total or 0) - (ivado_included_line_total or 0), 2)
            if line_total is not None
            else 0.0
        )
    # Receipt line totals adjust the claim only when the user has actually
    # deactivated a positive-value line. Otherwise the reviewed receipt total
    # remains the authoritative amount, including after an OCR correction.
    arvine_line_ratio = reviewed_line_ratio(
        line_total,
        arvine_included_line_total,
        arvine_excluded_line_total,
    )
    ivado_line_ratio = reviewed_line_ratio(
        line_total,
        ivado_included_line_total,
        arvine_excluded_line_total + ivado_excluded_line_total,
    )
    arvine_claim_ratio = arvine_line_ratio / people if included_in_arvine else 0.0
    ivado_claim_ratio = ivado_line_ratio / people if included_in_ivado else 0.0

    manual_cad = numeric(receipt.get("manual_cad_override"))
    cad_amount = manual_cad if manual_cad is not None else numeric(reconciled.get("cad_amount_used"))
    cad_source = "manual" if manual_cad is not None else str(reconciled.get("cad_source") or "")
    if cad_amount is None and str(receipt.get("currency") or "").upper() == "CAD":
        cad_amount = amount
        cad_source = "invoice"
    if not cad_source:
        cad_source = "unavailable"
    statement_purchase_amount = numeric(reconciled.get("statement_purchase_amount_used"))
    statement_purchase_currency = str(reconciled.get("statement_purchase_currency") or "").upper()
    expense_currency = str(receipt.get("currency") or "").upper()
    basis_expense = dict(receipt)
    basis_expense["amount"] = amount
    reconciled_basis = numeric(reconciled.get("fx_basis_amount_used"))
    reconciled_basis_status = str(reconciled.get("fx_basis_status") or "")
    if manual_cad is None and reconciled_basis not in (None, 0) and reconciled_basis_status:
        fx_basis, fx_basis_status = reconciled_basis, reconciled_basis_status
    else:
        fx_basis, fx_basis_status = accounting_basis(
            basis_expense,
            statement_purchase_amount if manual_cad is None else None,
            statement_purchase_currency if manual_cad is None else None,
        )
    if manual_cad is not None:
        fx_basis_status = f"manual_{fx_basis_status}"
    fx_rate = (
        round(abs(cad_amount) / abs(fx_basis), 6)
        if cad_amount is not None and fx_basis not in (None, 0)
        else None
    )
    arvine_original_basis = (
        arvine_included_line_total
        if arvine_excluded_line_total > 0.005
        and line_total not in (None, 0)
        and arvine_included_line_total is not None
        else amount or 0
    )
    ivado_original_basis = (
        ivado_included_line_total
        if (arvine_excluded_line_total + ivado_excluded_line_total) > 0.005
        and line_total not in (None, 0)
        and ivado_included_line_total is not None
        else amount or 0
    )
    arvine_claimable_original = (
        round(arvine_original_basis / people, 2) if included_in_arvine else 0.0
    )
    ivado_claimable_original = (
        round(ivado_original_basis / people, 2) if included_in_ivado else 0.0
    )
    direct_statement_statuses = {
        "statement_receipt_total",
        "statement_person_share",
        "statement_aggregated",
    }
    arvine_total_cad = program_cad_amount(
        included_in_arvine,
        cad_amount,
        manual_cad,
        fx_basis_status,
        direct_statement_statuses,
        arvine_line_ratio,
        arvine_claim_ratio,
        arvine_claimable_original,
        fx_rate,
    )
    ivado_claimable_cad = program_cad_amount(
        included_in_ivado,
        cad_amount,
        manual_cad,
        fx_basis_status,
        direct_statement_statuses,
        ivado_line_ratio,
        ivado_claim_ratio,
        ivado_claimable_original,
        fx_rate,
    )
    if claim_program != "ivado_sponsored":
        ivado_claimable_cad = 0.0
        ivado_excluded_cad = 0.0
    else:
        ivado_claimable_cad = min(
            arvine_total_cad or 0.0,
            ivado_claimable_cad or 0.0,
        )
        ivado_excluded_cad = round((arvine_total_cad or 0.0) - ivado_claimable_cad, 2)
    arvine_reimbursable_cad = (
        arvine_total_cad if paid_by == "employee_personal" else 0.0
    )
    corporate_paid_cad = (
        arvine_total_cad if paid_by == "arvine_corporate_bmo" else 0.0
    )
    claimable_cad = (
        ivado_claimable_cad
        if claim_program == "ivado_sponsored"
        else arvine_total_cad
    )
    return {
        "receipt_id": receipt.get("receipt_id"),
        "source_file": receipt["source_file"],
        "date": receipt.get("date"),
        "vendor": receipt.get("vendor"),
        "description": receipt.get("description"),
        "expense_type": receipt.get("expense_type"),
        "country": receipt.get("country"),
        "province": receipt.get("province"),
        "amount": amount,
        "subtotal": numeric(receipt.get("subtotal")),
        "currency": receipt.get("currency"),
        "paid_by": paid_by,
        "included": included_in_arvine,
        "included_in_arvine": included_in_arvine,
        "included_in_ivado": included_in_ivado,
        "ivado_exclusion_reason": receipt.get("ivado_exclusion_reason"),
        "number_of_people": people,
        "line_total": line_total,
        "included_line_total": arvine_included_line_total,
        "excluded_line_total": arvine_excluded_line_total,
        "arvine_included_line_total": arvine_included_line_total,
        "ivado_included_line_total": ivado_included_line_total,
        "arvine_claimable_ratio": round(arvine_claim_ratio, 8),
        "ivado_claimable_ratio": round(ivado_claim_ratio, 8),
        "claimable_ratio": round(arvine_claim_ratio, 8),
        "claimable_original": arvine_claimable_original,
        "arvine_claimable_original": arvine_claimable_original,
        "ivado_claimable_original": ivado_claimable_original,
        "cad_amount_used": cad_amount,
        "cad_source": cad_source,
        "fx_rate": fx_rate,
        "fx_basis_amount_used": fx_basis,
        "fx_basis_status": fx_basis_status,
        "statement_purchase_amount_used": statement_purchase_amount,
        "statement_purchase_currency": statement_purchase_currency or None,
        "claimable_cad": claimable_cad,
        "total_cad": arvine_total_cad,
        "arvine_reimbursable_cad": arvine_reimbursable_cad,
        "corporate_paid_cad": corporate_paid_cad,
        "ivado_claimable_cad": ivado_claimable_cad,
        "ivado_excluded_cad": ivado_excluded_cad,
        "arvine_excluded_cad": round(
            max(0.0, (cad_amount or 0.0) - (arvine_total_cad or 0.0)),
            2,
        ),
        "gst_hst": numeric(receipt.get("gst_hst")),
        "qst": numeric(receipt.get("qst")),
        "gst_hst_number": receipt.get("gst_hst_number"),
        "qst_number": receipt.get("qst_number"),
        "business_purpose": receipt.get("business_purpose"),
        "attendees_client": receipt.get("attendees_client"),
        "line_items": [dict(item) for item in receipt.get("line_items", []) if isinstance(item, dict)],
        "status": (
            "excluded"
            if not included_in_arvine
            else "ready"
            if cad_amount is not None
            else "review"
        ),
    }


def reviewed_line_ratio(
    line_total: float | None,
    included_total: float | None,
    excluded_total: float,
) -> float:
    if excluded_total > 0.005 and line_total not in (None, 0) and included_total is not None:
        return max(0.0, min(1.0, included_total / line_total))
    return 1.0


def program_cad_amount(
    included: bool,
    cad_amount: float | None,
    manual_cad: float | None,
    fx_basis_status: str,
    direct_statement_statuses: set[str],
    line_ratio: float,
    claim_ratio: float,
    claimable_original: float,
    fx_rate: float | None,
) -> float | None:
    if not included:
        return 0.0
    if (
        cad_amount is not None
        and manual_cad is None
        and fx_basis_status in direct_statement_statuses
    ):
        settlement_ratio = (
            line_ratio if fx_basis_status == "statement_person_share" else claim_ratio
        )
        return round(cad_amount * settlement_ratio, 2)
    return round(claimable_original * fx_rate, 2) if fx_rate is not None else None


def calculate_accounting_summary(expenses: list[dict], profile: dict, mode: str) -> dict:
    accounts: dict[str, float] = defaultdict(float)
    tax_recoverable = 0.0
    for expense in expenses:
        claimable = expense.get("arvine_reimbursable_cad")
        if claimable is None or not expense["included_in_arvine"]:
            continue
        is_meal = str(expense.get("expense_type") or "").startswith("meal")
        recovery_pct = (
            profile["meal_tax_recovery_pct"] if is_meal else profile["normal_tax_recovery_pct"]
        ) * profile["commercial_use_pct"]
        fx = expense.get("fx_rate") or (1.0 if expense.get("currency") == "CAD" else 0.0)
        share = expense["arvine_claimable_ratio"]
        gst_cad = round((expense.get("gst_hst") or 0) * fx * share, 2)
        qst_cad = round((expense.get("qst") or 0) * fx * share, 2)
        recoverable_gst = gst_cad * recovery_pct if profile["gst_hst_registrant"] else 0.0
        recoverable_qst = qst_cad * recovery_pct if profile["qst_registrant"] else 0.0
        tax_recoverable += recoverable_gst + recoverable_qst
        net = claimable - recoverable_gst - recoverable_qst
        if is_meal:
            deductible = net * profile["meal_deduction_pct"] * profile["commercial_use_pct"]
            accounts[profile["account_mapping"]["meal_deductible"]] += deductible
            accounts[profile["account_mapping"]["meal_nondeductible"]] += net - deductible
        else:
            accounts[profile["account_mapping"]["non_meal"]] += net
        accounts[profile["account_mapping"]["gst_hst_receivable"]] += recoverable_gst
        accounts[profile["account_mapping"]["qst_receivable"]] += recoverable_qst
    rows = [
        {"account": account, "amount_cad": round(amount, 2)}
        for account, amount in accounts.items()
        if abs(amount) >= 0.005
    ]
    journal_total = round(sum(row["amount_cad"] for row in rows), 2)
    claim_total = round(
        sum(expense.get("arvine_reimbursable_cad") or 0 for expense in expenses),
        2,
    )
    return {
        "available": True,
        "rows": rows,
        "journal_total": journal_total,
        "claim_total": claim_total,
        "balance_difference": round(journal_total - claim_total, 2),
        "recoverable_tax_cad": round(tax_recoverable, 2),
        "profile_version": profile["version"],
        "counter_account": profile["counter_account"],
    }


def finalize_consolidation(root: Path, trip_dir: Path) -> dict:
    view = consolidation_view(root, trip_dir)
    if not view["is_ready"]:
        raise ValueError(
            f"Resolve the {view['summary']['blocking_count']} blocking review item(s) before finalizing."
        )
    snapshot = review_input_snapshot(root, trip_dir)
    state = {
        "version": FINALIZATION_VERSION,
        "finalized_at": datetime.now().isoformat(timespec="seconds"),
        "input_fingerprint": snapshot["fingerprint"],
        "summary": view["summary"],
    }
    write_state_atomic(trip_dir / FINALIZATION_FILE, state)
    return finalization_view(root, trip_dir)


def finalization_view(root: Path, trip_dir: Path) -> dict:
    state = load_finalization_state(trip_dir)
    current_fingerprint = review_input_snapshot(root, trip_dir)["fingerprint"]
    return {
        "available": bool(state),
        "current": bool(state and state.get("input_fingerprint") == current_fingerprint),
        "finalized_at": state.get("finalized_at") if state else None,
        "input_fingerprint": state.get("input_fingerprint") if state else None,
    }


def ensure_consolidation_finalized(root: Path, trip_dir: Path) -> None:
    state = finalization_view(root, trip_dir)
    if not state["current"]:
        raise ValueError("Review the calculated results and finalize the current trip before exporting Excel.")


def load_finalization_state(trip_dir: Path) -> dict | None:
    path = trip_dir / FINALIZATION_FILE
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict) or state.get("version") != FINALIZATION_VERSION:
        return None
    return state


def write_state_atomic(path: Path, state: dict) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def add_issue(
    issues: list[dict],
    kind: str,
    severity: str,
    message: str,
    target: str,
    reference: str | None = None,
) -> None:
    issues.append(
        {
            "id": f"{kind}:{reference or len(issues) + 1}",
            "kind": kind,
            "severity": severity,
            "message": message,
            "target": target,
            "reference": reference,
        }
    )


def transaction_needs_review(transaction: dict) -> bool:
    if transaction.get("allocations"):
        return transaction.get("allocation_status") != "balanced"
    return bool(
        not transaction.get("expense_file")
        or transaction.get("normalization_status") in {"review", "possible_duplicate"}
        or (
            transaction.get("cad_completeness") != "complete"
            and transaction.get("cad_source") != "manual"
        )
    )


def numeric(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None
