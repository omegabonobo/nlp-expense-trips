from __future__ import annotations

import os
import subprocess
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from nlp_expenses.matching import match_transactions
from nlp_expenses.models import Expense, StatementTransaction


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


def build_workbook(trip_dir: Path, expenses: list[Expense], transactions: list[StatementTransaction]) -> Path:
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

    out_path = trip_dir / f"expense_review_{trip_dir.name}.xlsx"
    wb.save(out_path)
    remove_macos_metadata(out_path)
    return out_path


def append_clean(ws, values: list) -> None:
    ws.append([None if value == "" else value for value in values])


def write_expense_sheet(ws, expenses: list[Expense]) -> None:
    ws.append(EXPENSE_HEADERS)
    for idx, expense in enumerate(expenses, start=2):
        append_clean(
            ws,
            [
                expense.expense_id,
                expense.date,
                expense.supplier_name,
                expense.expense_type,
                f'=IF(COUNTIFS(expense_line_items!$A:$A,$A{idx},expense_line_items!$F:$F,">0")=0,"",SUMIFS(expense_line_items!$F:$F,expense_line_items!$A:$A,$A{idx}))',
                expense.amount,
                f'=IF(OR($E{idx}="",$F{idx}=""),"missing",IF(ABS($E{idx}-$F{idx})<=MAX(0.05,$F{idx}*0.03),"ok","mismatch"))',
                expense.currency,
                1,
                f'=IF($E{idx}="","",($E{idx}-SUMIFS(expense_line_items!$F:$F,expense_line_items!$A:$A,$A{idx},expense_line_items!$H:$H,TRUE))/IF(OR($I{idx}="",$I{idx}=0),1,$I{idx}))',
                f'=IFERROR(SUMIFS(card_statements!$C:$C,card_statements!$D:$D,$A{idx}),"")',
                f'=IF(OR($K{idx}="",$E{idx}="",$E{idx}=0),"",$K{idx}/($E{idx}/IF(OR($I{idx}="",$I{idx}=0),1,$I{idx})))',
                f'=IF(OR($J{idx}="",$L{idx}=""),"",$J{idx}*$L{idx})',
                expense.source_file.name,
                extraction_status(expense),
                expense.review_note,
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
        ws.conditional_formatting.add(
            f"K2:K{len(expenses) + 1}",
            CellIsRule(operator="equal", formula=["0"], fill=REVIEW_FILL, font=REVIEW_FONT),
        )
    for col in ["E", "F", "J", "K", "M"]:
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
        review_note = line_ws.cell(row, 11)
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
