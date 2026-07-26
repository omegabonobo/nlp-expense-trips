from __future__ import annotations

import os
import subprocess
import uuid
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
from openpyxl.utils import get_column_letter

from nlp_expenses.matching import apply_manual_matches, match_normalized_transactions, match_transactions
from nlp_expenses.models import Expense, NormalizedTransaction, StatementTransaction
from nlp_expenses.trip_metadata import trip_metadata


EXPENSE_HEADERS = [
    "expense_id",
    "date",
    "supplier_name",
    "expense_type",
    "amount_in_currency",
    "receipt_total_in_currency",
    "amount_check",
    "currency",
    "number_of_person",
    "corrected_amount_in_currency",
    "CAD_amount_statement",
    "CAD_rate",
    "corrected_CAD",
    "source_file",
    "extraction_status",
    "review_note",
    "include",
    "manual_CAD_override",
    "manual_CAD_note",
    "statement_purchase_amount",
    "statement_purchase_currency",
    "accounting_original_basis",
    "accounting_basis_status",
]
LINE_HEADERS = [
    "expense_id",
    "line_item_id",
    "date",
    "supplier_name",
    "description",
    "amount_in_currency",
    "currency",
    "is_alcohol",
    "included",
    "alcohol_detection_confidence",
    "alcohol_detection_reason",
    "alcohol_matched_term",
    "inclusion_overridden",
    "alcohol_overridden",
    "inclusion_note",
    "source_file",
    "confidence",
    "review_note",
]
STATEMENT_HEADERS = [
    "date",
    "description",
    "amount",
    "expense_id",
    "suggested_expense_id",
    "match_confidence",
    "source_file",
    "foreign_amount",
    "foreign_currency",
]

REVIEW_FILL = PatternFill("solid", fgColor="C00000")
REVIEW_FONT = Font(color="FFFFFF", bold=True)
CHECK_FILL = PatternFill("solid", fgColor="FFF2CC")

ARVINE_DETAIL_HEADERS = [
    "include",
    "expense_id",
    "invoice_date",
    "vendor",
    "description",
    "expense_type",
    "country",
    "province",
    "original_total",
    "currency",
    "GST/HST_in_currency",
    "QST_in_currency",
    "subtotal_excl_GST_QST_in_currency",
    "GST/HST_number",
    "QST_number",
    "tax_deductible_pct",
    "tax_credit_pct",
    "CAD_amount_statement",
    "CAD_statement_status",
    "manual_CAD_override",
    "CAD_amount_used",
    "FX_rate",
    "GST/HST_CAD",
    "QST_CAD",
    "recoverable_GST/HST_CAD",
    "recoverable_QST_CAD",
    "net_expense_CAD",
    "deductible_CAD",
    "non_deductible_CAD",
    "trip_purpose",
    "meal_attendees_client",
    "source_file",
    "extraction_status",
    "statement_match_status",
    "tax_documentation_status",
    "review_note",
    "line_item_review_status",
    "line_item_total",
    "included_line_total",
    "excluded_line_total",
    "claimable_pct",
    "number_of_people",
    "statement_purchase_amount",
    "statement_purchase_currency",
    "accounting_original_basis",
    "accounting_basis_status",
]

ARVINE_STATEMENT_HEADERS = [
    "transaction_group_id",
    "funding_leg_id",
    "transaction_date",
    "posted_date",
    "provider",
    "account_label",
    "cardholder",
    "description",
    "category",
    "transaction_type",
    "status",
    "direction",
    "match_eligible",
    "purchase_amount",
    "purchase_currency",
    "settlement_amount",
    "settlement_currency",
    "CAD_amount",
    "CAD_completeness",
    "expense_id",
    "suggested_expense_id",
    "match_status",
    "match_confidence",
    "source_file",
    "source_row",
    "normalization_status",
    "review_note",
]

ARVINE_SUMMARY_HEADERS = [
    "Invoice Date",
    "Vendor / Customer",
    "Description",
    "Account",
    "Counter-Account",
    "Currency",
    "FX Rate",
    "Subtotal (in currency)",
    "GST/HST (in currency)",
    "QST (in currency)",
    "Total with Tx (in currency)",
    "Subtotal (before tax) CAD",
    "GST/HST CAD",
    "QST CAD",
    "Amount total CAD",
    "GST #",
    "QST #",
]


def build_workbook(
    trip_dir: Path,
    expenses: list[Expense],
    transactions: list[StatementTransaction],
    output_path: Path | None = None,
    pre_matched: bool = False,
) -> Path:
    if not pre_matched:
        match_transactions(expenses, transactions)
    wb = Workbook()
    expense_ws = wb.active
    expense_ws.title = "expense_list"
    line_ws = wb.create_sheet("expense_line_items")
    statement_ws = wb.create_sheet("card_statements")

    write_expense_sheet(expense_ws, expenses)
    write_line_sheet(line_ws, expenses)
    write_statement_sheet(statement_ws, transactions)

    for ws in [expense_ws, line_ws, statement_ws]:
        style_sheet(ws)
    apply_review_highlights(expense_ws, line_ws, statement_ws)

    out_path = output_path or trip_dir / f"expense_review_{trip_dir.name}.xlsx"
    return save_workbook_atomic(wb, out_path)


def build_arvine_workbook(
    trip_dir: Path,
    expenses: list[Expense],
    transactions: list[NormalizedTransaction],
    output_path: Path | None = None,
    manual_matches: dict[str, str | None] | None = None,
    accounting_profile: dict | None = None,
    transaction_allocations: dict[str, list[dict]] | None = None,
) -> Path:
    """Build Arvine's accounting workbook without changing the IVADO workbook path."""
    match_normalized_transactions(expenses, transactions)
    if manual_matches:
        apply_manual_matches(expenses, transactions, manual_matches)
    transaction_allocations = transaction_allocations or {}
    apply_allocations_to_workbook_transactions(transactions, transaction_allocations)
    workbook = Workbook()
    detail_ws = workbook.active
    detail_ws.title = "expense_detail"
    summary_ws = workbook.create_sheet("expense_summary")
    statements_ws = workbook.create_sheet("card_statements")
    line_ws = workbook.create_sheet("expense_line_items")

    if accounting_profile is None:
        from nlp_expenses.accounting import builtin_accounting_profile

        accounting_profile = builtin_accounting_profile()
    write_arvine_detail_sheet(detail_ws, expenses, accounting_profile)
    write_arvine_summary_sheet(summary_ws, trip_dir, accounting_profile)
    write_arvine_statement_sheet(statements_ws, transactions, transaction_allocations, expenses)
    write_line_sheet(line_ws, expenses)
    style_sheet(line_ws)
    configure_arvine_calculation(workbook)

    out_path = output_path or trip_dir / f"expense_review_{trip_dir.name}.xlsx"
    return save_workbook_atomic(workbook, out_path)


def save_workbook_atomic(workbook: Workbook, out_path: Path) -> Path:
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.parent / f".{out_path.stem}.{uuid.uuid4().hex}.tmp.xlsx"
    try:
        workbook.save(temporary)
        os.replace(temporary, out_path)
    finally:
        temporary.unlink(missing_ok=True)
    remove_macos_metadata(out_path)
    return out_path


def expense_line_review_values(expense: Expense) -> dict:
    amounts = [item.amount for item in expense.line_items if item.amount is not None]
    included = [item.amount for item in expense.line_items if item.included and item.amount is not None]
    line_total = (
        expense.line_item_total
        if expense.line_item_total is not None
        else round(sum(amounts), 2) if amounts else None
    )
    included_total = (
        expense.included_line_total
        if expense.included_line_total is not None
        else round(sum(included), 2) if amounts else None
    )
    excluded_total = (
        expense.excluded_line_total
        if expense.excluded_line_total is not None
        else round((line_total or 0) - (included_total or 0), 2) if amounts else None
    )
    if expense.line_item_review_status:
        ratio = expense.claimable_ratio
        status = expense.line_item_review_status
    elif line_total and excluded_total and excluded_total > 0:
        ratio = max(0.0, min(1.0, (included_total or 0) / line_total))
        status = "automatic"
    else:
        ratio = 1.0
        status = "automatic" if amounts else "not_available"
    return {
        "status": status,
        "line_total": line_total,
        "included_total": included_total,
        "excluded_total": excluded_total,
        "claimable_ratio": round(ratio, 8),
    }


def write_arvine_detail_sheet(ws, expenses: list[Expense], accounting_profile: dict) -> None:
    ws.append(ARVINE_DETAIL_HEADERS)
    statement_end = max(5000, len(expenses) * 20 + 100)
    claimable_col = get_column_letter(ARVINE_DETAIL_HEADERS.index("claimable_pct") + 1)
    for row, expense in enumerate(expenses, start=2):
        line_review = expense_line_review_values(expense)
        is_meal = str(expense.expense_type or "").startswith("meal")
        claimable_formula = (
            f'=IF($A{row}=FALSE,0,IF(COUNTIFS(expense_line_items!$A:$A,$B{row},'
            f'expense_line_items!$I:$I,FALSE,expense_line_items!$F:$F,">0")>0,'
            f'IF(OR($AL{row}="",$AL{row}=0),1/MAX(1,$AP{row}),'
            f'MAX(0,MIN(1,$AM{row}/$AL{row}/MAX(1,$AP{row})))),'
            f'1/MAX(1,$AP{row})))'
        )
        claimable_original_formula = (
            f'IF(COUNTIFS(expense_line_items!$A:$A,$B{row},'
            f'expense_line_items!$I:$I,FALSE,expense_line_items!$F:$F,">0")>0,'
            f'$AM{row}/MAX(1,$AP{row}),$I{row}/MAX(1,$AP{row}))'
        )
        commercial_use = accounting_profile["commercial_use_pct"]
        deductible_pct = (
            accounting_profile["meal_deduction_pct"]
            if is_meal
            else accounting_profile["normal_deduction_pct"]
        ) * commercial_use
        base_tax_recovery = (
            accounting_profile["meal_tax_recovery_pct"]
            if is_meal
            else accounting_profile["normal_tax_recovery_pct"]
        ) * commercial_use
        gst_recovery_pct = base_tax_recovery if accounting_profile["gst_hst_registrant"] else 0.0
        qst_recovery_pct = base_tax_recovery if accounting_profile["qst_registrant"] else 0.0
        tax_credit_pct = max(gst_recovery_pct, qst_recovery_pct)
        append_clean(
            ws,
            [
                expense.included,
                expense.expense_id,
                excel_date(expense.date),
                expense.supplier_name,
                expense.description,
                expense.expense_type,
                expense.country,
                expense.province,
                expense.amount,
                expense.currency,
                expense.gst_hst,
                expense.qst,
                f'=IF($I{row}="","",IF(AND($K{row}="",$L{row}=""),$I{row},$I{row}-N($K{row})-N($L{row})))',
                expense.gst_hst_number,
                expense.qst_number,
                deductible_pct,
                tax_credit_pct,
                (
                    f'=IF(COUNTIFS(\'card_statements\'!$T$2:$T${statement_end},$B{row},\'card_statements\'!$M$2:$M${statement_end},TRUE)=0,"",'
                    f'IF(COUNTIFS(\'card_statements\'!$T$2:$T${statement_end},$B{row},\'card_statements\'!$M$2:$M${statement_end},TRUE,'
                    f'\'card_statements\'!$S$2:$S${statement_end},"<>complete")>0,"",'
                    f'SUMIFS(\'card_statements\'!$R$2:$R${statement_end},\'card_statements\'!$T$2:$T${statement_end},$B{row},'
                    f'\'card_statements\'!$M$2:$M${statement_end},TRUE)))'
                ),
                (
                    f'=IF($T{row}<>"","manual",'
                    f'IF(COUNTIFS(\'card_statements\'!$T$2:$T${statement_end},$B{row},\'card_statements\'!$M$2:$M${statement_end},TRUE)=0,"missing",'
                    f'IF(COUNTIFS(\'card_statements\'!$T$2:$T${statement_end},$B{row},\'card_statements\'!$M$2:$M${statement_end},TRUE,'
                    f'\'card_statements\'!$S$2:$S${statement_end},"<>complete")>0,"incomplete","complete")))'
                ),
                expense.manual_cad_override,
                (
                    f'=IF($T{row}<>"",ROUND($T{row}*${claimable_col}{row},2),'
                    f'IF(AND($R{row}<>"",$AS{row}<>""),'
                    f'ROUND(IF($AT{row}="statement_person_share",'
                    f'$R{row}*${claimable_col}{row}*MAX(1,$AP{row}),'
                    f'IF(OR($AT{row}="statement_receipt_total",$AT{row}="statement_aggregated"),'
                    f'$R{row}*${claimable_col}{row},$R{row}/$AS{row}*{claimable_original_formula})),2),'
                    f'IF($J{row}="CAD",ROUND($I{row}*${claimable_col}{row},2),"")))'
                ),
                (
                    f'=IF($I{row}="","",IF($J{row}="CAD",1,'
                    f'IFERROR(IF($T{row}<>"",$T{row}/$I{row},$R{row}/$AS{row}),"")))'
                ),
                f'=IF(OR($K{row}="",$V{row}=""),0,ROUND($K{row}*$V{row}*${claimable_col}{row},2))',
                f'=IF(OR($L{row}="",$V{row}=""),0,ROUND($L{row}*$V{row}*${claimable_col}{row},2))',
                f'=IF($U{row}="","",ROUND($W{row}*{gst_recovery_pct:.6f},2))',
                f'=IF($U{row}="","",ROUND($X{row}*{qst_recovery_pct:.6f},2))',
                f'=IF($U{row}="","",ROUND($U{row}-N($Y{row})-N($Z{row}),2))',
                f'=IF($AA{row}="","",IF($F{row}="meal",ROUND($AA{row}*$P{row},2),$AA{row}))',
                f'=IF($AA{row}="","",IF($F{row}="meal",$AA{row}-$AB{row},0))',
                expense.business_purpose,
                expense.attendees_client,
                expense.source_file.name,
                arvine_extraction_status(expense),
                (
                    f'=IF(COUNTIF(\'card_statements\'!$T$2:$T${statement_end},$B{row})=0,"unmatched",'
                    f'IF(COUNTIFS(\'card_statements\'!$T$2:$T${statement_end},$B{row},\'card_statements\'!$V$2:$V${statement_end},"auto")+'
                    f'COUNTIFS(\'card_statements\'!$T$2:$T${statement_end},$B{row},\'card_statements\'!$V$2:$V${statement_end},"manual")+'
                    f'COUNTIFS(\'card_statements\'!$T$2:$T${statement_end},$B{row},\'card_statements\'!$V$2:$V${statement_end},"allocation")+'
                    f'COUNTIFS(\'card_statements\'!$T$2:$T${statement_end},$B{row},\'card_statements\'!$V$2:$V${statement_end},"matched")'
                    f'>0,"matched","review"))'
                ),
                expense.tax_documentation_status or "review",
                (
                    f"{expense.review_note.rstrip()} Manual CAD override source: {expense.manual_cad_note}".strip()
                    if expense.manual_cad_override is not None
                    else expense.review_note
                ),
                line_review["status"],
                (
                    f'=IF(COUNTIF(expense_line_items!$A:$A,$B{row})=0,"",'
                    f'SUMIFS(expense_line_items!$F:$F,expense_line_items!$A:$A,$B{row}))'
                ),
                (
                    f'=IF($AL{row}="","",SUMIFS(expense_line_items!$F:$F,'
                    f'expense_line_items!$A:$A,$B{row},expense_line_items!$I:$I,TRUE))'
                ),
                f'=IF($AL{row}="","",$AL{row}-$AM{row})',
                claimable_formula,
                max(1, int(expense.number_of_people or 1)),
                (
                    f'=IF(COUNTIFS(\'card_statements\'!$T$2:$T${statement_end},$B{row},'
                    f'\'card_statements\'!$M$2:$M${statement_end},TRUE,'
                    f'\'card_statements\'!$N$2:$N${statement_end},"<>")=0,"",'
                    f'SUMIFS(\'card_statements\'!$N$2:$N${statement_end},'
                    f'\'card_statements\'!$T$2:$T${statement_end},$B{row},'
                    f'\'card_statements\'!$M$2:$M${statement_end},TRUE))'
                ),
                (
                    f'=IF($AQ{row}="","",IFERROR(INDEX(\'card_statements\'!$O$2:$O${statement_end},'
                    f'MATCH($B{row},\'card_statements\'!$T$2:$T${statement_end},0)),""))'
                ),
                (
                    f'=IF(OR($I{row}="",$I{row}=0),"",IF($T{row}<>"",$I{row},'
                    f'IF(AND($AQ{row}<>"",$AQ{row}<>0,$AR{row}=$J{row},'
                    f'OR(COUNTIFS(\'card_statements\'!$T$2:$T${statement_end},$B{row},'
                    f'\'card_statements\'!$M$2:$M${statement_end},TRUE)>1,'
                    f'OR(ABS($AQ{row}-$I{row})<=MAX(2,ABS($I{row})*0.08),'
                    f'AND($AP{row}>1,ABS($AQ{row}-$I{row}/$AP{row})'
                    f'<=MAX(2,ABS($I{row}/$AP{row})*0.08))))),$AQ{row},$I{row})))'
                ),
                (
                    f'=IF(OR($I{row}="",$I{row}=0),"unavailable",IF($T{row}<>"","manual_receipt_total",'
                    f'IF(OR($AQ{row}="",$AQ{row}=0),"receipt_total",'
                    f'IF($AR{row}<>$J{row},"receipt_fallback_currency",'
                    f'IF(COUNTIFS(\'card_statements\'!$T$2:$T${statement_end},$B{row},'
                    f'\'card_statements\'!$M$2:$M${statement_end},TRUE)>1,"statement_aggregated",'
                    f'IF(ABS($AQ{row}-$I{row})<=MAX(2,ABS($I{row})*0.08),"statement_receipt_total",'
                    f'IF(AND($AP{row}>1,ABS($AQ{row}-$I{row}/$AP{row})'
                    f'<=MAX(2,ABS($I{row}/$AP{row})*0.08)),'
                    f'"statement_person_share","receipt_fallback_mismatch")))))))'
                ),
            ],
        )
        source_cell = ws.cell(row, 32)
        source_cell.hyperlink = f"expenses_receipts/{expense.source_file.name}"
        source_cell.style = "Hyperlink"
        source_cell.font = Font(color="FF0000", underline="single")

    detail_end = max(len(expenses) + 1, 2)
    set_filter_range(ws, len(ARVINE_DETAIL_HEADERS), detail_end)
    ws.freeze_panes = "D2"
    ws.sheet_view.showGridLines = False
    style_table_header(ws, 1, len(ARVINE_DETAIL_HEADERS))
    add_arvine_detail_validations(ws, detail_end)
    style_arvine_detail_rows(ws, detail_end)
    add_arvine_detail_highlights(ws, detail_end)
    widths = [10, 20, 13, 24, 24, 14, 15, 10, 15, 10, 16, 14, 20, 19, 18, 17, 15, 18, 19, 20, 17, 12, 14, 13, 20, 17, 17, 16, 19, 25, 25, 26, 18, 21, 22, 48, 20, 16, 18, 18, 14, 18, 20, 18, 20, 26]
    set_widths(ws, widths)


def write_arvine_summary_sheet(ws, trip_dir: Path, accounting_profile: dict) -> None:
    trip_info = trip_metadata(trip_dir)
    traveller_company = " / ".join(
        value
        for value in (
            trip_info["company"] or accounting_profile["company_legal_name"],
            trip_info["traveller"],
        )
        if value
    )
    purpose_client = " / ".join(
        value for value in (trip_info["business_purpose"], trip_info["client_project"]) if value
    )
    metadata = [
        ("Report date", date.today()),
        ("Trip label", trip_dir.name),
        ("Company / traveller", traveller_company),
        ("Business purpose / client", purpose_client),
        ("Counter-account", accounting_profile["counter_account"]),
        ("Mode", "Arvine"),
    ]
    for row, (label, value) in enumerate(metadata, start=1):
        ws.cell(row, 1, label)
        ws.cell(row, 2, value)
    ws["D1"] = "Legend"
    ws["D2"] = "Editable input"
    ws["D3"] = "Linked formula"
    ws["D4"] = "Calculated formula"
    ws["D5"] = "Review warning"
    try:
        from nlp_expenses.reconciliation import load_reconciliation_state

        reconciliation_state = load_reconciliation_state(trip_dir) or {}
        coverage_confirmation = reconciliation_state.get("coverage_confirmation") or {}
    except Exception:
        coverage_confirmation = {}
    ws["D6"] = "Statement coverage"
    ws["E6"] = (
        f"Confirmed {coverage_confirmation.get('confirmed_at')}: "
        f"{coverage_confirmation.get('note') or 'No unresolved coverage gaps.'}"
        if coverage_confirmation
        else "Not confirmed"
    )
    ws["D7"] = "Coverage gaps"
    ws["E7"] = coverage_confirmation.get("gap_count", "")
    ws["S1"] = "Applied accounting assumptions"
    profile_snapshot = [
        ("Profile version", accounting_profile["version"]),
        ("Effective date", accounting_profile["effective_date"]),
        ("Reimbursement type", accounting_profile["traveller_reimbursement_type"]),
        ("GST/HST registrant", accounting_profile["gst_hst_registrant"]),
        ("QST registrant", accounting_profile["qst_registrant"]),
        ("Commercial use", accounting_profile["commercial_use_pct"]),
        ("Normal tax recovery", accounting_profile["normal_tax_recovery_pct"]),
        ("Meal tax recovery", accounting_profile["meal_tax_recovery_pct"]),
        ("Normal deduction", accounting_profile["normal_deduction_pct"]),
        ("Meal deduction", accounting_profile["meal_deduction_pct"]),
        ("Tax method", accounting_profile["tax_calculation_method"]),
    ]
    for profile_row, (label, value) in enumerate(profile_snapshot, start=2):
        ws.cell(profile_row, 19, label)
        ws.cell(profile_row, 20, value)
    ws["V1"] = "Trip metadata and policy"
    trip_snapshot = [
        ("Traveller", trip_info["traveller"]),
        ("Company", trip_info["company"]),
        ("Start date", trip_info["start_date"]),
        ("End date", trip_info["end_date"]),
        ("Origin", ", ".join(trip_info["origins"])),
        ("Destinations", ", ".join(trip_info["destinations"])),
        ("Business purpose", trip_info["business_purpose"]),
        ("Client / project", trip_info["client_project"]),
        ("Cost centre", trip_info["cost_centre"]),
        ("Approver", trip_info["approver"]),
        ("Payment method", trip_info["payment_method"]),
        ("Expected accounts", ", ".join(trip_info["expected_accounts"])),
        ("Policy profile", trip_info["policy_profile"]),
    ]
    for trip_row, (label, value) in enumerate(trip_snapshot, start=2):
        ws.cell(trip_row, 22, label)
        ws.cell(trip_row, 23, value)
    ws["V16"] = "Policy controls"
    policy_snapshot = [
        ("Receipt threshold CAD", trip_info["policy"]["receipt_required_threshold"]),
        ("Allowed categories", ", ".join(trip_info["policy"]["allowed_categories"])),
        ("Meal limit CAD", trip_info["policy"]["meal_limit_cad"]),
        ("Alcohol treatment", trip_info["policy"]["alcohol_treatment"]),
        ("Personal expense treatment", trip_info["policy"]["personal_expense_treatment"]),
        ("Mileage rate CAD", trip_info["policy"]["mileage_rate_cad"]),
        ("Per diem CAD", trip_info["policy"]["per_diem_cad"]),
        ("Statement buffer days", trip_info["policy"]["statement_coverage_buffer_days"]),
    ]
    for policy_row, (label, value) in enumerate(policy_snapshot, start=17):
        ws.cell(policy_row, 22, label)
        ws.cell(policy_row, 23, value)
    try:
        from nlp_expenses.reconciliation import reconciliation_view

        policy_warnings = reconciliation_view(trip_dir).get("policy_warnings", [])
    except Exception:
        policy_warnings = []
    ws["V26"] = "Policy warnings / exceptions"
    if policy_warnings:
        for warning_row, warning in enumerate(policy_warnings, start=27):
            ws.cell(warning_row, 22, warning["message"])
            ws.cell(
                warning_row,
                23,
                f"Exception: {warning['exception_note']}" if warning["resolved"] else "Needs review",
            )
    else:
        ws["V27"] = "No policy warnings"
    ws["S1"].font = Font(bold=True, color="1F4E78")
    ws["V1"].font = Font(bold=True, color="1F4E78")
    ws["V16"].font = Font(bold=True, color="1F4E78")
    ws["V26"].font = Font(bold=True, color="1F4E78")
    ws["D1"].font = Font(bold=True, color="1F4E78")
    ws["D2"].font = Font(color="0070C0")
    ws["D3"].font = Font(color="008000")
    ws["D4"].font = Font(color="000000")
    ws["D5"].fill = CHECK_FILL
    ws["D6"].font = Font(bold=True, color="1F4E78")
    ws["D7"].font = Font(bold=True, color="1F4E78")
    ws["E6"].alignment = Alignment(wrap_text=True, vertical="top")
    ws["B1"].number_format = "yyyy-mm-dd"
    ws["A8"] = "Trip-level CAD journal"
    ws["A8"].font = Font(size=14, bold=True, color="1F4E78")
    for col, header in enumerate(ARVINE_SUMMARY_HEADERS, start=1):
        ws.cell(9, col, header)

    mapping = accounting_profile["account_mapping"]
    accounts = [
        ("Airfare, accommodation, transport and other non-meal expense, excluding recoverable GST/QST", mapping["non_meal"]),
        ("Meal deductible portion, excluding recoverable GST/QST", mapping["meal_deductible"]),
        ("Meal non-deductible portion, excluding recoverable GST/QST", mapping["meal_nondeductible"]),
        ("Recoverable GST/HST paid on travel and meals", mapping["gst_hst_receivable"]),
        ("Recoverable QST paid on travel and meals", mapping["qst_receivable"]),
    ]
    amount_formulas = [
        '=SUMIFS(\'expense_detail\'!$AA$2:$AA$5000,\'expense_detail\'!$A$2:$A$5000,TRUE,\'expense_detail\'!$F$2:$F$5000,"<>meal")',
        '=SUMIFS(\'expense_detail\'!$AB$2:$AB$5000,\'expense_detail\'!$A$2:$A$5000,TRUE,\'expense_detail\'!$F$2:$F$5000,"meal")',
        '=SUMIFS(\'expense_detail\'!$AC$2:$AC$5000,\'expense_detail\'!$A$2:$A$5000,TRUE,\'expense_detail\'!$F$2:$F$5000,"meal")',
        '=SUMIFS(\'expense_detail\'!$Y$2:$Y$5000,\'expense_detail\'!$A$2:$A$5000,TRUE)',
        '=SUMIFS(\'expense_detail\'!$Z$2:$Z$5000,\'expense_detail\'!$A$2:$A$5000,TRUE)',
    ]
    for offset, ((description, account), amount_formula) in enumerate(zip(accounts, amount_formulas), start=10):
        ws.cell(offset, 1, "=$B$1")
        ws.cell(offset, 2, '=IF($B$3="","Expense report – "&$B$2,$B$3)')
        ws.cell(offset, 3, description)
        ws.cell(offset, 4, account)
        ws.cell(offset, 5, "=$B$5")
        ws.cell(offset, 6, "CAD")
        ws.cell(offset, 7, 1)
        if offset <= 12:
            ws.cell(offset, 12, amount_formula)
        elif offset == 13:
            ws.cell(offset, 13, amount_formula)
        else:
            ws.cell(offset, 14, amount_formula)
        ws.cell(offset, 15, amount_formula)
    ws.cell(15, 3, "Journal debit total")
    ws.cell(15, 15, "=SUM(O10:O14)")

    ws["A18"] = "Review checks"
    ws["A18"].font = Font(size=14, bold=True, color="1F4E78")
    checks = [
        ("Journal debits", "=O15", ""),
        ("Shareholder reimbursement", '=SUMIFS(\'expense_detail\'!$U$2:$U$5000,\'expense_detail\'!$A$2:$A$5000,TRUE)', ""),
        ("Journal balance difference", "=B20-B21", '=IF(ABS(B22)<0.01,"OK","REVIEW")'),
        ("Included receipts without a complete CAD amount", '=COUNTIFS(\'expense_detail\'!$A$2:$A$5000,TRUE,\'expense_detail\'!$U$2:$U$5000,"")', '=IF(B23=0,"OK","REVIEW")'),
        (
            "Included receipts without an acceptable statement match or manual override",
            '=COUNTIFS(\'expense_detail\'!$A$2:$A$5000,TRUE,\'expense_detail\'!$T$2:$T$5000,"",\'expense_detail\'!$AH$2:$AH$5000,"<>matched")',
            '=IF(B24=0,"OK","REVIEW")',
        ),
        ("Tax-documentation warnings", '=COUNTIFS(\'expense_detail\'!$A$2:$A$5000,TRUE,\'expense_detail\'!$AI$2:$AI$5000,"review")', '=IF(B25=0,"OK","REVIEW")'),
        ("Receipt-extraction warnings", '=COUNTIFS(\'expense_detail\'!$A$2:$A$5000,TRUE,\'expense_detail\'!$AG$2:$AG$5000,"<>ok")', '=IF(B26=0,"OK","REVIEW")'),
    ]
    for col, header in enumerate(("Check", "Value", "Status"), start=1):
        ws.cell(19, col, header)
    for row, check in enumerate(checks, start=20):
        for col, value in enumerate(check, start=1):
            ws.cell(row, col, value)

    style_table_header(ws, 9, len(ARVINE_SUMMARY_HEADERS))
    style_table_header(ws, 18 + 1, 3)
    for row in range(10, 16):
        ws.cell(row, 1).number_format = "yyyy-mm-dd"
        for col in range(7, 16):
            ws.cell(row, col).number_format = '#,##0.00;[Red]-#,##0.00;"-"'
    for row in range(10, 15):
        for col in (12, 13, 14, 15):
            if isinstance(ws.cell(row, col).value, str) and ws.cell(row, col).value.startswith("="):
                ws.cell(row, col).font = Font(color="008000")
    for row in range(20, 27):
        ws.cell(row, 2).number_format = '#,##0.00;[Red]-#,##0.00;"-"'
        ws.cell(row, 1).alignment = Alignment(vertical="top", wrap_text=True)
        ws.row_dimensions[row].height = 32 if row != 24 else 45
    for row in (21, 23, 24, 25, 26):
        ws.cell(row, 2).font = Font(color="008000")
    input_fill = PatternFill("solid", fgColor="DDEBF7")
    for row in range(1, 7):
        ws.cell(row, 1).font = Font(bold=True, color="1F4E78")
        ws.cell(row, 2).fill = input_fill
        ws.cell(row, 2).font = Font(color="0070C0")
    ws.conditional_formatting.add("C22:C26", FormulaRule(formula=['$C22="REVIEW"'], fill=CHECK_FILL))
    ws.freeze_panes = "A9"
    ws.sheet_view.showGridLines = False
    ws.auto_filter.ref = "A9:Q15"
    set_widths(ws, [32, 28, 58, 30, 30, 12, 12, 18, 18, 16, 19, 22, 16, 14, 20, 16, 16])
    ws.column_dimensions["S"].width = 25
    ws.column_dimensions["T"].width = 34
    ws.column_dimensions["V"].width = 34
    ws.column_dimensions["W"].width = 42


def apply_allocations_to_workbook_transactions(
    transactions: list[NormalizedTransaction],
    allocations_by_group: dict[str, list[dict]],
) -> None:
    for transaction in transactions:
        if not allocations_by_group.get(transaction.transaction_group_id):
            continue
        transaction.match_eligible = False
        transaction.expense_id = ""
        transaction.suggested_expense_id = ""
        transaction.match_status = "allocated_raw"
        transaction.review_note = (
            f"{transaction.review_note.rstrip()} Raw transaction retained; reimbursement is driven by allocation rows."
        ).strip()


def write_arvine_statement_sheet(
    ws,
    transactions: list[NormalizedTransaction],
    allocations_by_group: dict[str, list[dict]] | None = None,
    expenses: list[Expense] | None = None,
) -> None:
    allocations_by_group = allocations_by_group or {}
    expenses_by_file = {expense.source_file.name: expense for expense in (expenses or [])}
    ws.append(ARVINE_STATEMENT_HEADERS)
    previous_group = None
    group_fill = None
    for row, transaction in enumerate(transactions, start=2):
        if transaction.transaction_group_id != previous_group:
            group_fill = PatternFill("solid", fgColor="F3F6FA" if row % 2 == 0 else "FFFFFF")
        append_clean(
            ws,
            [
                transaction.transaction_group_id,
                transaction.funding_leg_id,
                excel_date(transaction.transaction_date),
                excel_date(transaction.posted_date),
                transaction.provider,
                transaction.account_label,
                transaction.cardholder,
                transaction.description,
                transaction.category,
                transaction.transaction_type,
                transaction.status,
                transaction.direction,
                transaction.match_eligible,
                transaction.purchase_amount,
                transaction.purchase_currency,
                transaction.settlement_amount,
                transaction.settlement_currency,
                transaction.cad_amount,
                transaction.cad_completeness,
                transaction.expense_id,
                transaction.suggested_expense_id,
                transaction.match_status,
                transaction.match_confidence,
                transaction.source_file.name,
                transaction.source_row,
                transaction.normalization_status,
                transaction.review_note,
            ],
        )
        for col in range(1, len(ARVINE_STATEMENT_HEADERS) + 1):
            ws.cell(row, col).fill = group_fill
        if previous_group is not None and transaction.transaction_group_id != previous_group:
            border = Border(top=Side(style="thin", color="A6A6A6"))
            for col in range(1, len(ARVINE_STATEMENT_HEADERS) + 1):
                ws.cell(row, col).border = border
        source_cell = ws.cell(row, 24)
        source_cell.hyperlink = f"card_statements/{transaction.source_file.name}"
        source_cell.style = "Hyperlink"
        source_cell.font = Font(color="FF0000", underline="single")
        previous_group = transaction.transaction_group_id

    representatives: dict[str, NormalizedTransaction] = {}
    cad_by_group: dict[str, float] = {}
    for transaction in transactions:
        representatives.setdefault(transaction.transaction_group_id, transaction)
        if transaction.cad_amount is not None:
            cad_by_group[transaction.transaction_group_id] = round(
                cad_by_group.get(transaction.transaction_group_id, 0.0) + transaction.cad_amount,
                2,
            )
    for group_id, allocations in allocations_by_group.items():
        representative = representatives.get(group_id)
        if not representative:
            continue
        target_cad = cad_by_group.get(group_id)
        allocation_total = sum(float(item.get("cad_amount") or 0) for item in allocations)
        status = (
            "balanced"
            if target_cad is not None and abs(target_cad - allocation_total) <= 0.01
            else "review"
        )
        for allocation in allocations:
            expense = expenses_by_file.get(str(allocation.get("invoice_file") or ""))
            reimbursable = allocation.get("type") in {"purchase", "refund", "fee"} and expense is not None
            note_parts = [
                f"Allocation {allocation.get('allocation_id')} ({allocation.get('type')}).",
                str(allocation.get("note") or allocation.get("category") or "").strip(),
            ]
            append_clean(
                ws,
                [
                    group_id,
                    f"{group_id}:allocation:{allocation.get('allocation_id')}",
                    excel_date(representative.transaction_date),
                    excel_date(representative.posted_date),
                    "allocation",
                    representative.account_label,
                    representative.cardholder,
                    representative.description,
                    allocation.get("category"),
                    allocation.get("type"),
                    "allocated",
                    "in" if float(allocation.get("cad_amount") or 0) < 0 else "out",
                    reimbursable,
                    allocation.get("original_amount"),
                    representative.purchase_currency,
                    allocation.get("cad_amount"),
                    "CAD",
                    allocation.get("cad_amount"),
                    "complete",
                    expense.expense_id if reimbursable else None,
                    None,
                    "allocation",
                    float(allocation.get("percentage") or 0) / 100,
                    representative.source_file.name,
                    representative.source_row,
                    status,
                    " ".join(part for part in note_parts if part),
                ],
            )
            allocation_row = ws.max_row
            source_cell = ws.cell(allocation_row, 24)
            source_cell.hyperlink = f"card_statements/{representative.source_file.name}"
            source_cell.style = "Hyperlink"
            source_cell.font = Font(color="FF0000", underline="single")

    end_row = max(ws.max_row, 2)
    set_filter_range(ws, len(ARVINE_STATEMENT_HEADERS), end_row)
    ws.freeze_panes = "H2"
    ws.sheet_view.showGridLines = False
    style_table_header(ws, 1, len(ARVINE_STATEMENT_HEADERS))
    for row in range(2, end_row + 1):
        for col in (20, 22):
            ws.cell(row, col).fill = PatternFill("solid", fgColor="DDEBF7")
            ws.cell(row, col).font = Font(color="0070C0")
            ws.cell(row, col).protection = Protection(locked=False)
        for col in (3, 4):
            ws.cell(row, col).number_format = "yyyy-mm-dd"
        for col in (14, 16, 18):
            ws.cell(row, col).number_format = '#,##0.00;[Red]-#,##0.00;"-"'
        ws.cell(row, 23).number_format = "0%"
        ws.cell(row, 27).alignment = Alignment(vertical="top", wrap_text=True)
        if ws.cell(row, 27).value:
            ws.row_dimensions[row].height = 32
    if transactions or allocations_by_group:
        status_validation = DataValidation(type="list", formula1='"unmatched,suggested,auto,manual,matched,ignored,allocation,allocated_raw"')
        ws.add_data_validation(status_validation)
        status_validation.add(f"V2:V{end_row}")
    ws.conditional_formatting.add(
        f"A2:AA{end_row}",
        FormulaRule(formula=['AND($M2=TRUE,$T2="")'], fill=PatternFill("solid", fgColor="FFF2CC")),
    )
    ws.conditional_formatting.add(
        f"S2:S{end_row}",
        FormulaRule(formula=['OR($S2="partial",$S2="incomplete")'], fill=PatternFill("solid", fgColor="FCE4D6")),
    )
    ws.conditional_formatting.add(
        f"Z2:Z{end_row}",
        FormulaRule(formula=['$Z2<>"ok"'], fill=PatternFill("solid", fgColor="FFD966")),
    )
    ws.conditional_formatting.add(
        f"J2:J{end_row}",
        FormulaRule(formula=['$J2="refund"'], fill=PatternFill("solid", fgColor="E2F0D9")),
    )
    ws.protection.sheet = True
    ws.protection.autoFilter = False
    ws.protection.sort = False
    set_widths(ws, [27, 31, 14, 14, 12, 14, 20, 32, 18, 16, 14, 12, 14, 17, 15, 18, 17, 15, 18, 22, 22, 16, 16, 28, 12, 20, 55])


def add_arvine_detail_validations(ws, end_row: int) -> None:
    if end_row < 2:
        return
    boolean_validation = DataValidation(type="list", formula1='"TRUE,FALSE"')
    type_validation = DataValidation(type="list", formula1='"flight,hotel,transport,meal,other"')
    currency_validation = DataValidation(type="list", formula1='"CAD,USD,AUD,EUR,GBP,IDR,VND,QAR,HKD,CHF"')
    pct_validation = DataValidation(type="decimal", operator="between", formula1="0", formula2="1")
    people_validation = DataValidation(type="whole", operator="between", formula1="1", formula2="99")
    for validation, target in [
        (boolean_validation, f"A2:A{end_row}"),
        (type_validation, f"F2:F{end_row}"),
        (currency_validation, f"J2:J{end_row}"),
        (pct_validation, f"P2:Q{end_row}"),
        (people_validation, f"AP2:AP{end_row}"),
    ]:
        ws.add_data_validation(validation)
        validation.add(target)


def style_arvine_detail_rows(ws, end_row: int) -> None:
    input_columns = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 17, 20, 30, 31, 35, 36, 42}
    formula_columns = {
        13, 18, 19, 21, 22, 23, 24, 25, 26, 27, 28, 29, 34, 38, 39, 40, 41, 43, 44, 45, 46
    }
    for row in range(2, end_row + 1):
        for col in input_columns:
            ws.cell(row, col).fill = PatternFill("solid", fgColor="DDEBF7")
            ws.cell(row, col).font = Font(color="0070C0")
        for col in formula_columns:
            ws.cell(row, col).font = (
                Font(color="008000") if col in {18, 19, 34, 38, 39, 40, 41} else Font(color="000000")
            )
        ws.cell(row, 3).number_format = "yyyy-mm-dd"
        for col in (9, 11, 12, 13, 18, 20, 21, 23, 24, 25, 26, 27, 28, 29, 43, 45):
            ws.cell(row, col).number_format = '#,##0.00;[Red]-#,##0.00;"-"'
        for col in (38, 39, 40):
            ws.cell(row, col).number_format = '#,##0.00;[Red]-#,##0.00;"-"'
        for col in (16, 17):
            ws.cell(row, col).number_format = "0%"
        ws.cell(row, 41).number_format = "0.00%"
        ws.cell(row, 22).number_format = "0.000000"
        for col in range(1, len(ARVINE_DETAIL_HEADERS) + 1):
            ws.cell(row, col).alignment = Alignment(vertical="top", wrap_text=col in {5, 30, 31, 36})


def add_arvine_detail_highlights(ws, end_row: int) -> None:
    ws.conditional_formatting.add(
        f"S2:S{end_row}",
        FormulaRule(formula=['OR($S2="missing",$S2="incomplete")'], fill=PatternFill("solid", fgColor="FCE4D6")),
    )
    ws.conditional_formatting.add(
        f"AH2:AH{end_row}",
        FormulaRule(formula=['$AH2<>"matched"'], fill=PatternFill("solid", fgColor="FFF2CC")),
    )
    ws.conditional_formatting.add(
        f"AI2:AI{end_row}",
        FormulaRule(formula=['$AI2="review"'], fill=PatternFill("solid", fgColor="FCE4D6")),
    )
    ws.conditional_formatting.add(
        f"AG2:AG{end_row}",
        FormulaRule(formula=['$AG2<>"ok"'], fill=PatternFill("solid", fgColor="FCE4D6")),
    )
    ws.conditional_formatting.add(
        f"AK2:AK{end_row}",
        FormulaRule(formula=['OR($AK2="review",$AK2="not_available")'], fill=PatternFill("solid", fgColor="FFF2CC")),
    )
    ws.conditional_formatting.add(
        f"AT2:AT{end_row}",
        FormulaRule(
            formula=['OR($AT2="receipt_fallback_mismatch",$AT2="receipt_fallback_currency")'],
            fill=PatternFill("solid", fgColor="FFF2CC"),
        ),
    )


def style_table_header(ws, row: int, width: int) -> None:
    header_fill = PatternFill("solid", fgColor="1F4E78")
    for col in range(1, width + 1):
        cell = ws.cell(row, col)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 32


def set_widths(ws, widths: list[int]) -> None:
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width


def excel_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return value


def arvine_extraction_status(expense: Expense) -> str:
    if expense.amount is None or not expense.currency:
        return "manual"
    if not expense.date or not expense.supplier_name or expense.supplier_name == "Unknown supplier":
        return "review"
    return "ok"


def configure_arvine_calculation(workbook: Workbook) -> None:
    calculation = getattr(workbook, "calculation", None)
    if calculation is None:
        return
    calculation.calcMode = "auto"
    calculation.fullCalcOnLoad = True
    calculation.forceFullCalc = True


def append_clean(ws, values: list) -> None:
    ws.append([None if value == "" else value for value in values])


def write_expense_sheet(ws, expenses: list[Expense]) -> None:
    ws.append(EXPENSE_HEADERS)
    for idx, expense in enumerate(expenses, start=2):
        corrected_original_formula = (
            f'=IF($Q{idx}=FALSE,0,IF($F{idx}="","",'
            f'IF(COUNTIFS(expense_line_items!$A:$A,$A{idx},expense_line_items!$I:$I,FALSE,'
            f'expense_line_items!$F:$F,">0")>0,'
            f'SUMIFS(expense_line_items!$F:$F,expense_line_items!$A:$A,$A{idx},'
            f'expense_line_items!$I:$I,TRUE)/IF(OR($I{idx}="",$I{idx}=0),1,$I{idx}),'
            f'$F{idx}/IF(OR($I{idx}="",$I{idx}=0),1,$I{idx}))))'
        )
        line_ratio_formula = (
            f'IF(COUNTIFS(expense_line_items!$A:$A,$A{idx},expense_line_items!$I:$I,FALSE,'
            f'expense_line_items!$F:$F,">0")>0,'
            f'IF(OR($E{idx}="",$E{idx}=0),1,$J{idx}*MAX(1,$I{idx})/$E{idx}),1)'
        )
        append_clean(
            ws,
            [
                expense.expense_id,
                expense.date,
                expense.supplier_name,
                expense.expense_type,
                f'=IF(COUNTIFS(expense_line_items!$A:$A,$A{idx},expense_line_items!$F:$F,">0")=0,"",SUMIFS(expense_line_items!$F:$F,expense_line_items!$A:$A,$A{idx}))',
                expense.amount,
                (
                    f'=IF($F{idx}="","missing",IF($E{idx}="","receipt total used",'
                    f'IF(ABS($E{idx}-$F{idx})<=MAX(0.05,$F{idx}*0.03),"ok","mismatch")))'
                ),
                expense.currency,
                max(1, int(expense.number_of_people or 1)),
                corrected_original_formula,
                (
                    f'=IF($R{idx}<>"",$R{idx},IF(COUNTIF(card_statements!$D:$D,$A{idx})=0,"",'
                    f'SUMIFS(card_statements!$C:$C,card_statements!$D:$D,$A{idx})))'
                ),
                (
                    f'=IF(OR($K{idx}="",$K{idx}=0),"",'
                    f'IF($H{idx}="CAD",1,IF(OR($V{idx}="",$V{idx}=0),"",$K{idx}/$V{idx})))'
                ),
                (
                    f'=IF(OR($J{idx}="",$L{idx}=""),"",'
                    f'IF($W{idx}="statement_person_share",$K{idx}*{line_ratio_formula},'
                    f'IF(OR($W{idx}="statement_receipt_total",$W{idx}="statement_aggregated"),'
                    f'$K{idx}*{line_ratio_formula}/MAX(1,$I{idx}),$J{idx}*$L{idx})))'
                ),
                expense.source_file.name,
                extraction_status(expense),
                expense.review_note,
                expense.included,
                expense.manual_cad_override,
                expense.manual_cad_note,
                (
                    f'=IF($H{idx}="CAD",'
                    f'IF(COUNTIF(card_statements!$D:$D,$A{idx})=0,"",'
                    f'SUMIFS(card_statements!$C:$C,card_statements!$D:$D,$A{idx})),'
                    f'IF(COUNTIFS(card_statements!$D:$D,$A{idx},card_statements!$H:$H,">0")=0,"",'
                    f'SUMIFS(card_statements!$H:$H,card_statements!$D:$D,$A{idx})))'
                ),
                (
                    f'=IF($T{idx}="","",IF($H{idx}="CAD","CAD",'
                    f'IFERROR(INDEX(card_statements!$I:$I,MATCH($A{idx},card_statements!$D:$D,0)),"")))'
                ),
                (
                    f'=IF($R{idx}<>"",IF(AND($F{idx}<>"",$F{idx}<>0),$F{idx},$E{idx}),'
                    f'IF(OR($F{idx}="",$F{idx}=0),$E{idx},'
                    f'IF(OR($T{idx}="",$T{idx}=0),$F{idx},'
                    f'IF($U{idx}<>$H{idx},$F{idx},'
                    f'IF(OR(COUNTIF(card_statements!$D:$D,$A{idx})>1,'
                    f'ABS($T{idx}-$F{idx})<=MAX(2,ABS($F{idx})*0.08),'
                    f'AND($I{idx}>1,ABS($T{idx}-$F{idx}/$I{idx})'
                    f'<=MAX(2,ABS($F{idx}/$I{idx})*0.08))),$T{idx},$F{idx})))))'
                ),
                (
                    f'=IF($R{idx}<>"","manual_receipt_total",'
                    f'IF(OR($T{idx}="",$T{idx}=0),"receipt_total",'
                    f'IF($U{idx}<>$H{idx},"receipt_fallback_currency",'
                    f'IF(COUNTIF(card_statements!$D:$D,$A{idx})>1,"statement_aggregated",'
                    f'IF(ABS($T{idx}-$F{idx})<=MAX(2,ABS($F{idx})*0.08),"statement_receipt_total",'
                    f'IF(AND($I{idx}>1,ABS($T{idx}-$F{idx}/$I{idx})'
                    f'<=MAX(2,ABS($F{idx}/$I{idx})*0.08)),'
                    f'"statement_person_share","receipt_fallback_mismatch"))))))'
                ),
            ]
        )
    set_filter_range(ws, len(EXPENSE_HEADERS), max(len(expenses) + 1, 2))
    ws.freeze_panes = "A2"
    if expenses:
        validation = DataValidation(type="whole", operator="between", formula1="1", formula2="99", allow_blank=False)
        validation.error = "Enter a whole number from 1 to 99."
        validation.errorTitle = "Invalid number of persons"
        ws.add_data_validation(validation)
        validation.add(f"I2:I{len(expenses) + 1}")
        include_validation = DataValidation(type="list", formula1='"TRUE,FALSE"', allow_blank=False)
        ws.add_data_validation(include_validation)
        include_validation.add(f"Q2:Q{len(expenses) + 1}")
        ws.conditional_formatting.add(
            f"K2:K{len(expenses) + 1}",
            CellIsRule(operator="equal", formula=["0"], fill=REVIEW_FILL, font=REVIEW_FONT),
        )
    for col in ["E", "F", "J", "K", "M", "T", "V"]:
        for cell in ws[col][1:]:
            if cell.value is not None:
                cell.number_format = "#,##0.00"
    for cell in ws["L"][1:]:
        if cell.value is not None:
            cell.number_format = "0.00000"


def write_line_sheet(ws, expenses: list[Expense]) -> None:
    ws.append(LINE_HEADERS)
    row_count = 0
    for expense in expenses:
        items = expense.line_items or []
        if not items:
            items = [fallback_line_item(expense)]
        for item_idx, item in enumerate(items, start=1):
            append_clean(
                ws,
                [
                    expense.expense_id,
                    f"{expense.expense_id}-L{item_idx:03d}",
                    expense.date,
                    expense.supplier_name,
                    item.description,
                    item.amount,
                    expense.currency,
                    item.is_alcohol,
                    item.included,
                    item.alcohol_confidence,
                    item.alcohol_reason,
                    item.alcohol_matched_term,
                    item.inclusion_overridden,
                    item.alcohol_overridden,
                    item.inclusion_note,
                    expense.source_file.name,
                    item.confidence,
                    item.review_note,
                ]
            )
            row_count += 1
    set_filter_range(ws, len(LINE_HEADERS), max(row_count + 1, 2))
    ws.freeze_panes = "A2"
    for cell in ws["F"][1:]:
        if cell.value is not None:
            cell.number_format = "#,##0.00"
    if row_count:
        validation = DataValidation(type="list", formula1='"TRUE,FALSE"', allow_blank=False)
        validation.error = "Choose TRUE or FALSE."
        validation.errorTitle = "Invalid line-item choice"
        ws.add_data_validation(validation)
        validation.add(f"H2:I{row_count + 1}")


def write_statement_sheet(ws, transactions: list[StatementTransaction]) -> None:
    ws.append(STATEMENT_HEADERS)
    for transaction in transactions:
        append_clean(
            ws,
            [
                transaction.date,
                transaction.description,
                transaction.amount_cad,
                transaction.expense_id,
                transaction.suggested_expense_id,
                transaction.match_confidence or "",
                transaction.source_file.name,
                transaction.foreign_amount,
                transaction.foreign_currency,
            ]
        )
    set_filter_range(ws, len(STATEMENT_HEADERS), max(len(transactions) + 1, 2))
    ws.freeze_panes = "A2"
    for col in ["C", "H"]:
        for cell in ws[col][1:]:
            if cell.value is not None:
                cell.number_format = "#,##0.00"


def style_sheet(ws) -> None:
    header_fill = PatternFill("solid", fgColor="1F4E78")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
    widths = {
        "A": 28,
        "B": 12,
        "C": 30,
        "D": 18,
        "E": 16,
        "F": 20,
        "G": 14,
        "H": 12,
        "I": 16,
        "J": 20,
        "K": 20,
        "L": 14,
        "M": 16,
        "N": 32,
        "O": 12,
        "P": 48,
    }
    for col_idx in range(1, ws.max_column + 1):
        letter = get_column_letter(col_idx)
        ws.column_dimensions[letter].width = widths.get(letter, 18)


def apply_review_highlights(expense_ws, line_ws, statement_ws) -> None:
    for row in range(2, expense_ws.max_row + 1):
        for col in (2, 3, 4, 5, 6, 8):
            cell = expense_ws.cell(row, col)
            if cell.value in (None, ""):
                mark_review(cell)
        amount_check = expense_ws.cell(row, 7)
        if amount_check.value != "ok":
            amount_check.fill = CHECK_FILL
        status = expense_ws.cell(row, 15)
        if status.value in {"review", "manual"}:
            mark_review(status)
        review_note = expense_ws.cell(row, 16)
        if review_note.value:
            mark_review(review_note)
        expense_ws.cell(row, 9).fill = CHECK_FILL

    for row in range(2, statement_ws.max_row + 1):
        expense_id = statement_ws.cell(row, 4)
        if expense_id.value in (None, ""):
            mark_review(expense_id)

    for row in range(2, line_ws.max_row + 1):
        for col in (5, 6):
            cell = line_ws.cell(row, col)
            if cell.value in (None, ""):
                mark_review(cell)
        review_note = line_ws.cell(row, LINE_HEADERS.index("review_note") + 1)
        if review_note.value:
            review_note.fill = CHECK_FILL


def mark_review(cell) -> None:
    cell.fill = REVIEW_FILL
    cell.font = REVIEW_FONT


def fallback_line_item(expense: Expense):
    class FallbackLineItem:
        description = "Receipt total" if expense.amount is not None else "Receipt total missing - review"
        amount = expense.amount
        is_alcohol = False
        included = True
        alcohol_confidence = 0.0
        alcohol_reason = "no line-item classification available"
        alcohol_matched_term = ""
        inclusion_overridden = False
        alcohol_overridden = False
        inclusion_note = ""
        confidence = 0.0
        review_note = "Generated workbook fallback line item."

    return FallbackLineItem()


def extraction_status(expense: Expense) -> str:
    if expense.amount is None or "No extractable text" in expense.review_note:
        return "manual"
    if not expense.date or not expense.supplier_name or not expense.expense_type:
        return "review"
    if expense.review_note:
        return "review"
    return "ok"


def set_filter_range(ws, width: int, height: int) -> None:
    end_col = get_column_letter(width)
    ws.auto_filter.ref = f"A1:{end_col}{height}"


def remove_macos_metadata(path: Path) -> None:
    for attr in ("com.apple.quarantine", "com.apple.provenance", "com.apple.lastuseddate#PS"):
        try:
            os.removexattr(path, attr)
        except (AttributeError, OSError):
            pass
        try:
            subprocess.run(["xattr", "-d", attr, str(path)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
