from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from nlp_expenses.workbook_common import (
    save_workbook_atomic,
    set_filter_range,
    style_table_header,
)


def build_reimbursement_report_workbook(
    trip_dir: Path,
    records: list[dict],
    output_path: Path,
) -> Path:
    """Build a compact, human-readable Arvine expense report from contract v3."""

    receipts, report = split_manifest_records(records)
    workbook = Workbook()
    report_ws = workbook.active
    report_ws.title = "Expense Report"
    accounting_ws = workbook.create_sheet("Accounting Rows")

    write_minimal_arvine_report_sheet(report_ws, trip_dir, report, receipts)
    write_minimal_accounting_sheet(accounting_ws, report)
    style_minimal_report_workbook(workbook)
    return save_workbook_atomic(workbook, output_path)


def build_ivado_claim_workbook(
    trip_dir: Path,
    records: list[dict],
    output_path: Path,
) -> Path:
    """Build the three-tab IVADO workbook requested for copy/paste submission."""

    receipts, report = split_manifest_records(records)
    if report.get("claim_program") != "ivado_sponsored":
        raise ValueError("An IVADO workbook is only produced for IVADO-sponsored trips.")

    workbook = Workbook()
    report_ws = workbook.active
    report_ws.title = "Expense Report"
    statements_ws = workbook.create_sheet("Card Statements")
    items_ws = workbook.create_sheet("Receipt Items")

    write_ivado_expense_report_sheet(report_ws, report, receipts)
    write_consolidated_statement_sheet(statements_ws, trip_dir)
    write_ivado_receipt_items_sheet(items_ws, receipts)
    style_ivado_workbook(workbook)
    return save_workbook_atomic(workbook, output_path)


MINIMAL_ARVINE_HEADERS = [
    "Receipt #",
    "Date",
    "Vendor",
    "Description",
    "Category",
    "Receipt File",
    "Employees Sharing",
    "Currency",
    "Full Receipt",
    "Traveller Share",
    "Reviewed CAD",
    "Paid By",
    "Employee Reimbursement CAD",
    "Corporate Paid CAD",
    "IVADO Claim CAD",
    "IVADO Removed CAD",
]

IVADO_REPORT_HEADERS = [
    "Sequence",
    "Invoice Date",
    "Supplier Name",
    "Description of Expense",
    "Location of Expense",
    "Receipt / Invoice",
    "Original Amount (foreign transactions)",
    "Total CAD incl. taxes & tip",
    "Office expenses GL 426",
    "Conferences GL 429",
    "Training GL 432",
    "Memberships GL 435",
    "Travel GL 439",
    "Travel - Food GL 449",
    "Food - Employees GL 447",
    "Other expenses",
    "Other GL / note",
    "GST",
    "QST",
    "HST",
    "Other tax",
    "Allocated total",
    "Difference",
]

IVADO_STATEMENT_HEADERS = [
    "Transaction Date",
    "Provider",
    "Account",
    "Description",
    "Original Amount",
    "Currency",
    "CAD Amount",
    "Matched Receipt",
    "Match Status",
    "Source Statement(s)",
    "CAD Conversion Rate",
    "Rate Week Start",
    "Rate Week End",
    "Conversion Method",
    "Conversion Route",
    "Rate Source",
    "Rate Source URL(s)",
]

IVADO_ITEM_HEADERS = [
    "Receipt #",
    "Receipt Date",
    "Vendor",
    "Receipt File",
    "Employees Sharing",
    "Line ID",
    "Receipt Item",
    "Full Invoice Item",
    "Traveller Share",
    "Currency",
    "Alcohol",
    "Included in Arvine",
    "Included in IVADO",
    "Removed from IVADO (original)",
    "Removed from IVADO (CAD)",
    "Removal Reason",
    "Review Note",
]


def write_minimal_arvine_report_sheet(
    ws,
    trip_dir: Path,
    report: dict,
    receipts: list[dict],
) -> None:
    ws["A1"] = "Arvine Trip Expense Report"
    ws["A3"] = "Traveller"
    ws["B3"] = report.get("traveller")
    ws["D3"] = "Report date"
    ws["E3"] = report.get("report_date")
    ws["A4"] = "Purpose"
    ws["B4"] = report.get("description")
    ws["D4"] = "Claim program"
    ws["E4"] = report.get("claim_program")
    ws["A5"] = "Trip"
    ws["B5"] = trip_dir.name
    ws["D5"] = "Contract"
    ws["E5"] = "trip-reimbursement-manifest.v3.ndjson"
    ws.append([])
    ws.append(MINIMAL_ARVINE_HEADERS)
    header_row = 7
    for sequence, receipt in enumerate(receipts, start=1):
        people = max(1, int(receipt.get("number_of_people") or 1))
        traveller_share = round(float(receipt.get("total") or 0) / people, 2)
        ws.append(
            [
                sequence,
                receipt.get("document_date"),
                receipt.get("vendor"),
                receipt.get("description"),
                receipt.get("expense_type"),
                receipt.get("source_file"),
                people,
                receipt.get("currency"),
                receipt.get("total"),
                traveller_share,
                receipt.get("total_cad"),
                readable_paid_by(receipt.get("paid_by")),
                receipt.get("arvine_reimbursable_cad"),
                (
                    receipt.get("total_cad")
                    if receipt.get("paid_by") == "arvine_corporate_bmo"
                    else 0.0
                ),
                receipt.get("ivado_claimable_cad"),
                receipt.get("ivado_excluded_cad"),
            ]
        )
        source_cell = ws.cell(ws.max_row, 6)
        source_cell.hyperlink = f"expenses_receipts/{receipt.get('source_file')}"
        source_cell.style = "Hyperlink"
    total_row = ws.max_row + 1
    ws.cell(total_row, 1, "TOTAL")
    for column in (9, 10, 11, 13, 14, 15, 16):
        letter = get_column_letter(column)
        ws.cell(total_row, column, f"=SUM({letter}{header_row + 1}:{letter}{total_row - 1})")
    ws.freeze_panes = f"A{header_row + 1}"
    set_filter_range(ws, len(MINIMAL_ARVINE_HEADERS), max(total_row - 1, header_row + 1))


def write_minimal_accounting_sheet(ws, report: dict) -> None:
    ws.append(["Accounting component", "Amount CAD"])
    labels = [
        ("Travel / non-meal", "travel_non_meal_cad"),
        ("Meals - deductible", "meal_deductible_cad"),
        ("Meals - non-deductible", "meal_non_deductible_cad"),
        ("GST/HST receivable", "gst_receivable_cad"),
        ("QST receivable", "qst_receivable_cad"),
    ]
    for label, key in labels:
        ws.append([label, report.get("accounting_summary", {}).get(key, 0.0)])
    ws.append(["TOTAL", "=SUM(B2:B6)"])
    ws.append(["Employee reimbursement", report.get("employee_reimbursement_total_cad")])
    ws.append(["Difference", "=ROUND(B7-B8,2)"])
    ws.append(["Status", '=IF(ABS(B9)<=0.02,"PASS","REVIEW")'])
    ws.freeze_panes = "A2"


def write_ivado_expense_report_sheet(ws, report: dict, receipts: list[dict]) -> None:
    ws["A1"] = "Rapport de Dépense - Expense Report"
    ws["A3"] = "Nom de l'employé / Employee Name"
    ws["D3"] = report.get("traveller")
    ws["A4"] = "Date / Report Date"
    ws["D4"] = report.get("report_date")
    ws["A6"] = (
        "Rows below mirror IVADO's expense-entry columns. "
        "Only the reviewed IVADO share is included; removed items remain visible in Receipt Items."
    )
    ws.merge_cells("A6:W6")
    header_row = 8
    for column, value in enumerate(IVADO_REPORT_HEADERS, start=1):
        ws.cell(header_row, column, value)

    sequence = 0
    for receipt in receipts:
        claim_cad = round(float(receipt.get("ivado_claimable_cad") or 0), 2)
        if claim_cad <= 0:
            continue
        sequence += 1
        row = header_row + sequence
        tax_values = ivado_tax_values(receipt, claim_cad)
        category_column = ivado_category_column(receipt.get("expense_type"))
        category_amount = round(max(0.0, claim_cad - sum(tax_values.values())), 2)
        description = str(receipt.get("description") or receipt.get("expense_type") or "Expense")
        if float(receipt.get("ivado_excluded_cad") or 0) > 0.005:
            description = f"{description} — removed items in Receipt Items"
        values = (
            [
                sequence,
                receipt.get("document_date"),
                receipt.get("vendor"),
                description,
                ivado_location(receipt),
                "Yes",
                (
                    ivado_original_claim_amount(receipt)
                    if str(receipt.get("currency") or "").upper() != "CAD"
                    else None
                ),
                claim_cad,
            ]
            + [0.0] * 9
            + [
                tax_values["gst"],
                tax_values["qst"],
                tax_values["hst"],
                tax_values["other"],
            ]
        )
        values[category_column - 1] = category_amount
        for column, value in enumerate(values, start=1):
            ws.cell(row, column, value)
        ws.cell(row, 22, f"=SUM(I{row}:U{row})")
        ws.cell(row, 23, f"=ROUND(V{row}-H{row},2)")

    first_data_row = header_row + 1
    last_data_row = max(ws.max_row, first_data_row)
    total_row = last_data_row + 1
    ws.cell(total_row, 1, "TOTAL")
    for column in range(8, 23):
        letter = get_column_letter(column)
        ws.cell(total_row, column, f"=SUM({letter}{first_data_row}:{letter}{last_data_row})")
    ws.cell(total_row, 23, f"=ROUND(V{total_row}-H{total_row},2)")
    ws.freeze_panes = f"A{first_data_row}"
    set_filter_range(ws, len(IVADO_REPORT_HEADERS), last_data_row)


def write_consolidated_statement_sheet(ws, trip_dir: Path) -> None:
    ws.append(IVADO_STATEMENT_HEADERS)
    try:
        from nlp_expenses.reconciliation import reconciliation_view

        transactions = reconciliation_view(trip_dir).get("transactions", [])
    except Exception:
        transactions = []
    for transaction in transactions:
        source_files = transaction.get("source_files") or []
        ws.append(
            [
                transaction.get("transaction_date"),
                transaction.get("provider"),
                transaction.get("account_label"),
                transaction.get("description"),
                transaction.get("purchase_amount"),
                transaction.get("purchase_currency"),
                transaction.get("cad_amount"),
                transaction.get("expense_file"),
                transaction.get("match_status"),
                ", ".join(str(value) for value in source_files),
                transaction.get("cad_conversion_rate"),
                transaction.get("cad_conversion_week_start"),
                transaction.get("cad_conversion_week_end"),
                transaction.get("cad_conversion_method"),
                transaction.get("cad_conversion_route"),
                transaction.get("cad_conversion_source"),
                ", ".join(
                    str(value) for value in transaction.get("cad_conversion_source_urls", [])
                ),
            ]
        )
        if source_files:
            source_cell = ws.cell(ws.max_row, 10)
            source_cell.hyperlink = f"card_statements/{source_files[0]}"
            source_cell.style = "Hyperlink"
    ws.freeze_panes = "A2"
    set_filter_range(ws, len(IVADO_STATEMENT_HEADERS), max(ws.max_row, 2))


def write_ivado_receipt_items_sheet(ws, receipts: list[dict]) -> None:
    ws.append(IVADO_ITEM_HEADERS)
    for sequence, receipt in enumerate(receipts, start=1):
        people = max(1, int(receipt.get("number_of_people") or 1))
        items = receipt.get("line_items") or [
            {
                "line_id": f"{receipt.get('id')}-TOTAL",
                "description": receipt.get("description") or "Receipt total",
                "amount": receipt.get("total") or 0.0,
                "is_alcohol": False,
                "included_in_arvine": receipt.get("included_in_arvine", True),
                "included_in_ivado": receipt.get("included_in_ivado", True),
                "ivado_exclusion_reason": receipt.get("ivado_exclusion_reason"),
                "review_note": "No separate receipt items were extracted.",
            }
        ]
        removed_items = [
            item
            for item in items
            if item.get("included_in_arvine", True) and not item.get("included_in_ivado", True)
        ]
        removed_original_total = sum(
            float(item.get("amount") or 0) / people for item in removed_items
        )
        removed_cad_total = float(receipt.get("ivado_excluded_cad") or 0)
        for item in items:
            amount = float(item.get("amount") or 0)
            share_amount = round(amount / people, 2)
            removed = item.get("included_in_arvine", True) and not item.get(
                "included_in_ivado", True
            )
            removed_original = share_amount if removed else 0.0
            removed_cad = (
                round(removed_cad_total * (removed_original / removed_original_total), 2)
                if removed and removed_original_total > 0
                else 0.0
            )
            ws.append(
                [
                    sequence,
                    receipt.get("document_date"),
                    receipt.get("vendor"),
                    receipt.get("source_file"),
                    people,
                    item.get("line_id"),
                    item.get("description"),
                    amount,
                    share_amount,
                    receipt.get("currency"),
                    "Yes" if item.get("is_alcohol") else "No",
                    "Yes" if item.get("included_in_arvine", True) else "No",
                    "Yes" if item.get("included_in_ivado", True) else "No",
                    removed_original,
                    removed_cad,
                    item.get("ivado_exclusion_reason"),
                    item.get("review_note"),
                ]
            )
            source_cell = ws.cell(ws.max_row, 4)
            source_cell.hyperlink = f"expenses_receipts/{receipt.get('source_file')}"
            source_cell.style = "Hyperlink"
            if removed:
                for cell in ws[ws.max_row]:
                    cell.fill = PatternFill("solid", fgColor="FFF2CC")
                if item.get("is_alcohol"):
                    ws.cell(ws.max_row, 11).fill = PatternFill("solid", fgColor="F4CCCC")
                    ws.cell(ws.max_row, 11).font = Font(color="9C0006", bold=True)
    ws.freeze_panes = "A2"
    set_filter_range(ws, len(IVADO_ITEM_HEADERS), max(ws.max_row, 2))


def ivado_original_claim_amount(receipt: dict) -> float:
    people = max(1, int(receipt.get("number_of_people") or 1))
    items = receipt.get("line_items") or []
    excluded = any(
        item.get("included_in_arvine", True) and not item.get("included_in_ivado", True)
        for item in items
    )
    if excluded:
        included_total = sum(
            float(item.get("amount") or 0)
            for item in items
            if item.get("included_in_arvine", True) and item.get("included_in_ivado", True)
        )
        if included_total > 0:
            return round(included_total / people, 2)
    return round(float(receipt.get("total") or 0) / people, 2)


def ivado_tax_values(receipt: dict, claim_cad: float) -> dict[str, float]:
    result = {"gst": 0.0, "qst": 0.0, "hst": 0.0, "other": 0.0}
    if not ivado_is_canadian(receipt):
        return result
    original_total = float(receipt.get("total") or 0)
    fx_rate = float(receipt.get("fx_rate") or (1.0 if receipt.get("currency") == "CAD" else 0.0))
    if original_total <= 0 or fx_rate <= 0:
        return result
    claim_ratio = ivado_original_claim_amount(receipt) / original_total
    gst_hst_cad = round(float(receipt.get("gst") or 0) * fx_rate * claim_ratio, 2)
    qst_cad = round(float(receipt.get("qst") or 0) * fx_rate * claim_ratio, 2)
    province = str(receipt.get("province") or "").strip().upper()
    if province in {
        "ON",
        "ONTARIO",
        "NB",
        "NEW BRUNSWICK",
        "NL",
        "NEWFOUNDLAND",
        "NS",
        "NOVA SCOTIA",
        "PE",
        "PEI",
        "PRINCE EDWARD ISLAND",
    }:
        result["hst"] = min(claim_cad, gst_hst_cad)
    else:
        result["gst"] = min(claim_cad, gst_hst_cad)
        result["qst"] = min(max(0.0, claim_cad - result["gst"]), qst_cad)
    return result


def ivado_is_canadian(receipt: dict) -> bool:
    country = str(receipt.get("country") or "").strip().upper()
    province = str(receipt.get("province") or "").strip()
    if country in {"CANADA", "CA", "CAN"}:
        return True
    if country and country not in {"CANADA", "CA", "CAN"}:
        return False
    return bool(province) or str(receipt.get("currency") or "").upper() == "CAD"


def ivado_location(receipt: dict) -> str:
    if not ivado_is_canadian(receipt):
        return "NR - Outside Canada"
    province = str(receipt.get("province") or "").strip().upper()
    labels = {
        "QC": "QC - Québec",
        "QUEBEC": "QC - Québec",
        "QUÉBEC": "QC - Québec",
        "ON": "ON - Ontario",
        "ONTARIO": "ON - Ontario",
        "AB": "AB - Alberta",
        "ALBERTA": "AB - Alberta",
        "BC": "BC - British Columbia",
        "BRITISH COLUMBIA": "BC - British Columbia",
        "MB": "MB - Manitoba",
        "MANITOBA": "MB - Manitoba",
        "NB": "NB - New Brunswick",
        "NEW BRUNSWICK": "NB - New Brunswick",
        "NL": "NL - Newfoundland and Labrador",
        "NEWFOUNDLAND": "NL - Newfoundland and Labrador",
        "NS": "NS - Nova Scotia",
        "NOVA SCOTIA": "NS - Nova Scotia",
        "PE": "PE - Prince Edward Island",
        "PEI": "PE - Prince Edward Island",
        "SK": "SK - Saskatchewan",
        "SASKATCHEWAN": "SK - Saskatchewan",
    }
    return labels.get(province, f"{province} - Canada" if province else "Canada - province review")


def ivado_category_column(expense_type: object) -> int:
    value = str(expense_type or "").strip().lower()
    if "conference" in value:
        return 10
    if "training" in value:
        return 11
    if "membership" in value or "subscription" in value:
        return 12
    if "meal" in value or "food" in value or "restaurant" in value:
        return 14
    if any(
        token in value
        for token in ("flight", "hotel", "transport", "taxi", "uber", "parking", "travel")
    ):
        return 13
    if "office" in value:
        return 9
    return 16


def readable_paid_by(value: object) -> str:
    return "Employee personal" if value == "employee_personal" else "Arvine corporate BMO"


def style_minimal_report_workbook(workbook: Workbook) -> None:
    for ws in workbook.worksheets:
        ws.sheet_view.showGridLines = False
        for row in ws.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for column in range(1, ws.max_column + 1):
            letter = get_column_letter(column)
            max_length = max(
                (len(str(cell.value)) for cell in ws[letter] if cell.value not in (None, "")),
                default=10,
            )
            ws.column_dimensions[letter].width = min(max(max_length + 2, 12), 38)
    report_ws = workbook["Expense Report"]
    report_ws["A1"].font = Font(bold=True, size=18, color="1F4E78")
    report_ws.merge_cells("A1:P1")
    style_table_header(report_ws, 7, len(MINIMAL_ARVINE_HEADERS))
    total_row = report_ws.max_row
    for cell in report_ws[total_row]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9EAF7")
    for column in (9, 10, 11, 13, 14, 15, 16):
        for cell in report_ws[get_column_letter(column)][7:]:
            cell.number_format = "#,##0.00;[Red]-#,##0.00"
    accounting_ws = workbook["Accounting Rows"]
    style_table_header(accounting_ws, 1, 2)
    accounting_ws.column_dimensions["A"].width = 28
    accounting_ws.column_dimensions["B"].width = 18
    for cell in accounting_ws["B"][1:]:
        cell.number_format = "#,##0.00;[Red]-#,##0.00"
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.calculation.calcMode = "auto"


def style_ivado_workbook(workbook: Workbook) -> None:
    for ws in workbook.worksheets:
        ws.sheet_view.showGridLines = False
        for row in ws.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for column in range(1, ws.max_column + 1):
            letter = get_column_letter(column)
            max_length = max(
                (len(str(cell.value)) for cell in ws[letter] if cell.value not in (None, "")),
                default=10,
            )
            ws.column_dimensions[letter].width = min(max(max_length + 2, 11), 34)
    report_ws = workbook["Expense Report"]
    report_ws["A1"].font = Font(bold=True, size=18, color="1F4E78")
    report_ws.merge_cells("A1:W1")
    for label_cell in ("A3", "A4"):
        report_ws[label_cell].font = Font(bold=True)
        report_ws[label_cell].fill = PatternFill("solid", fgColor="FFF2CC")
    report_ws["D3"].fill = PatternFill("solid", fgColor="FFF2CC")
    report_ws["D4"].fill = PatternFill("solid", fgColor="FFF2CC")
    report_ws["A6"].fill = PatternFill("solid", fgColor="EAF3F8")
    style_table_header(report_ws, 8, len(IVADO_REPORT_HEADERS))
    total_row = report_ws.max_row
    for cell in report_ws[total_row]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9EAF7")
    for column in range(7, 24):
        for cell in report_ws[get_column_letter(column)][8:]:
            cell.number_format = "#,##0.00;[Red]-#,##0.00"
    report_ws.conditional_formatting.add(
        f"W9:W{total_row}",
        CellIsRule(
            operator="notBetween",
            formula=["-0.02", "0.02"],
            fill=PatternFill("solid", fgColor="F4CCCC"),
            font=Font(color="9C0006", bold=True),
        ),
    )
    for ws_name, header_count, money_columns in (
        ("Card Statements", len(IVADO_STATEMENT_HEADERS), (5, 7)),
        ("Receipt Items", len(IVADO_ITEM_HEADERS), (8, 9, 14, 15)),
    ):
        ws = workbook[ws_name]
        style_table_header(ws, 1, header_count)
        for column in money_columns:
            for cell in ws[get_column_letter(column)][1:]:
                cell.number_format = "#,##0.00;[Red]-#,##0.00"
        if ws_name == "Card Statements":
            for cell in ws["K"][1:]:
                cell.number_format = "0.00000000"
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.calculation.calcMode = "auto"


def split_manifest_records(records: list[dict]) -> tuple[list[dict], dict]:
    receipts = [record for record in records if record.get("kind") == "receipt"]
    reports = [record for record in records if record.get("kind") == "trip_report"]
    if len(reports) != 1:
        raise ValueError("Expected exactly one trip report record.")
    return receipts, reports[0]
