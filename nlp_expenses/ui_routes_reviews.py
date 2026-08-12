from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request
from werkzeug.local import LocalProxy

from nlp_expenses.consolidation import (
    consolidation_view,
    ensure_consolidation_finalized,
    finalize_consolidation,
)
from nlp_expenses.jobs import JobManager
from nlp_expenses.line_items import (
    add_line_item,
    ensure_line_item_review_ready,
    line_item_review_view,
    remove_line_item,
    reset_receipt_review,
    set_expense_review,
    set_line_item_review,
)
from nlp_expenses.reconciliation import (
    confirm_statement_coverage,
    ensure_reconciliation_ready,
    reconciliation_view,
    set_coverage_settings,
    set_invoice_review,
    set_manual_match,
    set_transaction_allocations,
    set_transaction_decision,
)
from nlp_expenses.trips import trip_mode
from nlp_expenses.ui_services import (
    resolve_trip,
    trip_details,
)
from nlp_expenses.ui_status import receipt_quality, system_status

root: Path = LocalProxy(lambda: current_app.config["ROOT_PATH"])
jobs: JobManager = LocalProxy(lambda: current_app.extensions["generation_jobs"])

review_routes = Blueprint("review", __name__)


@review_routes.post("/api/trips/<trip_name>/generate")
def generate(trip_name: str):
    data = request.get_json(silent=True) or {}
    statements_complete = bool(data.get("statements_complete"))
    details = trip_details(root, trip_name)
    trip = resolve_trip(root, trip_name)
    if not details["receipts"]:
        raise ValueError("Add at least one receipt before generating the workbook.")
    if details["mode"] == "arvine" and details["statement_errors"]:
        raise ValueError("Fix the statement validation errors before generating the workbook.")
    ensure_reconciliation_ready(trip)
    ensure_consolidation_finalized(root, trip)
    if details["mode"] == "arvine":
        # Statement completeness was confirmed as part of the finalized app review.
        statements_complete = True
    quality = receipt_quality(ensure_line_item_review_ready(trip))
    if quality == "best" and not system_status(root)["openai_configured"]:
        raise ValueError(
            "The receipt scan used Best quality. Restore the OpenAI API key before generating."
        )
    job = jobs.start(trip_name, quality, statements_complete)
    return jsonify({"job": job.to_dict()}), 202


@review_routes.post("/api/trips/<trip_name>/reconcile")
def reconcile(trip_name: str):
    details = trip_details(root, trip_name)
    trip = resolve_trip(root, trip_name)
    if not details["receipts"]:
        raise ValueError("Add at least one invoice or receipt before syncing.")
    if not details["statements"]:
        raise ValueError("Add at least one card or bank statement before syncing.")
    if details["statement_errors"]:
        raise ValueError("Fix the statement validation errors before syncing.")
    review = line_item_review_view(trip)
    if not review["available"] or review["stale"]:
        raise ValueError("Scan the current receipts in Step 3 before reconciliation.")
    quality = receipt_quality(review)
    if quality == "best" and not system_status(root)["openai_configured"]:
        raise ValueError(
            "The receipt scan used Best quality. Restore the OpenAI API key before reconciling."
        )
    job = jobs.start_reconciliation(trip_name, quality)
    return jsonify({"job": job.to_dict()}), 202


@review_routes.post("/api/trips/<trip_name>/line-items/sync")
def sync_line_items(trip_name: str):
    data = request.get_json(silent=True) or {}
    quality = str(data.get("quality", "basic")).lower()
    details = trip_details(root, trip_name)
    if not details["receipts"]:
        raise ValueError("Add at least one receipt before scanning line items.")
    if quality == "best" and not system_status(root)["openai_configured"]:
        raise ValueError("Save an OpenAI API key before selecting Best quality.")
    job = jobs.start_line_item_review(trip_name, quality)
    return jsonify({"job": job.to_dict()}), 202


@review_routes.post("/api/trips/<trip_name>/line-items/item")
def update_line_item(trip_name: str):
    data = request.get_json(silent=True) or {}
    fields = data.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("Line-item fields must be submitted as an object.")
    with jobs.mutation_guard(trip_name):
        view = set_line_item_review(
            resolve_trip(root, trip_name),
            str(data.get("source_file", "")),
            str(data.get("line_id", "")),
            fields,
        )
    return jsonify({"line_item_review": view, "trip": trip_details(root, trip_name)})


@review_routes.post("/api/trips/<trip_name>/line-items/expense")
def update_expense(trip_name: str):
    data = request.get_json(silent=True) or {}
    fields = data.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("Expense fields must be submitted as an object.")
    with jobs.mutation_guard(trip_name):
        view = set_expense_review(
            resolve_trip(root, trip_name),
            str(data.get("source_file", "")),
            fields,
        )
    return jsonify(
        {
            "line_item_review": view,
            "consolidation": consolidation_view(root, resolve_trip(root, trip_name)),
        }
    )


@review_routes.post("/api/trips/<trip_name>/line-items/add")
def add_receipt_line(trip_name: str):
    data = request.get_json(silent=True) or {}
    with jobs.mutation_guard(trip_name):
        view = add_line_item(
            resolve_trip(root, trip_name),
            str(data.get("source_file", "")),
            str(data.get("description", "")),
            data.get("amount"),
            included=bool(data.get("included", True)),
            is_alcohol=bool(data.get("is_alcohol", False)),
        )
    return jsonify({"line_item_review": view})


@review_routes.post("/api/trips/<trip_name>/line-items/remove")
def remove_receipt_line(trip_name: str):
    data = request.get_json(silent=True) or {}
    with jobs.mutation_guard(trip_name):
        view = remove_line_item(
            resolve_trip(root, trip_name),
            str(data.get("source_file", "")),
            str(data.get("line_id", "")),
        )
    return jsonify({"line_item_review": view})


@review_routes.post("/api/trips/<trip_name>/line-items/reset")
def reset_line_items(trip_name: str):
    data = request.get_json(silent=True) or {}
    with jobs.mutation_guard(trip_name):
        view = reset_receipt_review(
            resolve_trip(root, trip_name),
            str(data.get("source_file", "")),
        )
    return jsonify({"line_item_review": view, "trip": trip_details(root, trip_name)})


@review_routes.get("/api/trips/<trip_name>/reconciliation")
def get_reconciliation(trip_name: str):
    trip = resolve_trip(root, trip_name)
    return jsonify({"reconciliation": reconciliation_view(trip)})


@review_routes.get("/api/trips/<trip_name>/consolidation")
def get_consolidation(trip_name: str):
    return jsonify({"consolidation": consolidation_view(root, resolve_trip(root, trip_name))})


@review_routes.post("/api/trips/<trip_name>/finalize")
def finalize_review(trip_name: str):
    data = request.get_json(silent=True) or {}
    with jobs.mutation_guard(trip_name):
        trip = resolve_trip(root, trip_name)
        if trip_mode(trip) == "arvine":
            if not bool(data.get("statements_complete")):
                raise ValueError("Confirm that all card and bank statements have been added.")
            if trip_details(root, trip_name)["statements"]:
                confirm_statement_coverage(
                    trip,
                    str(data.get("coverage_acknowledgement", "")),
                )
        finalization = finalize_consolidation(root, trip)
    return jsonify(
        {
            "finalization": finalization,
            "consolidation": consolidation_view(root, trip),
            "trip": trip_details(root, trip_name),
        }
    )


@review_routes.post("/api/trips/<trip_name>/reconciliation/mapping")
def update_reconciliation_mapping(trip_name: str):
    data = request.get_json(silent=True) or {}
    group_id = str(data.get("group_id", "")).strip()
    if not group_id:
        raise ValueError("Choose a statement transaction to update.")
    expense_file = data.get("expense_file")
    if expense_file is not None:
        expense_file = str(expense_file)
    with jobs.mutation_guard(trip_name):
        view = set_manual_match(
            resolve_trip(root, trip_name),
            group_id,
            expense_file=expense_file,
            use_auto=bool(data.get("use_auto")),
        )
    return jsonify({"reconciliation": view})


@review_routes.post("/api/trips/<trip_name>/reconciliation/invoice")
def update_reconciliation_invoice(trip_name: str):
    data = request.get_json(silent=True) or {}
    source_file = str(data.get("source_file", "")).strip()
    if not source_file:
        raise ValueError("Choose an invoice to update.")
    fields = data.get("fields")
    if fields is not None and not isinstance(fields, dict):
        raise ValueError("Invoice fields must be submitted as an object.")
    with jobs.mutation_guard(trip_name):
        view = set_invoice_review(
            resolve_trip(root, trip_name),
            source_file,
            fields=fields,
            restore_extracted=bool(data.get("restore_extracted")),
            update_manual_cad=bool(data.get("update_manual_cad")),
            manual_cad=data.get("manual_cad"),
        )
    return jsonify({"reconciliation": view})


@review_routes.post("/api/trips/<trip_name>/reconciliation/transaction-decision")
def update_transaction_decision(trip_name: str):
    data = request.get_json(silent=True) or {}
    group_id = str(data.get("group_id", "")).strip()
    if not group_id:
        raise ValueError("Choose a statement transaction to review.")
    with jobs.mutation_guard(trip_name):
        view = set_transaction_decision(
            resolve_trip(root, trip_name),
            group_id,
            str(data.get("action", "")),
            str(data.get("note", "")),
        )
    return jsonify({"reconciliation": view})


@review_routes.post("/api/trips/<trip_name>/reconciliation/allocations")
def update_transaction_allocations(trip_name: str):
    data = request.get_json(silent=True) or {}
    group_id = str(data.get("group_id", "")).strip()
    allocations = data.get("allocations", [])
    if not group_id:
        raise ValueError("Choose a statement transaction to allocate.")
    if not isinstance(allocations, list):
        raise ValueError("Allocations must be submitted as a list.")
    with jobs.mutation_guard(trip_name):
        view = set_transaction_allocations(
            resolve_trip(root, trip_name),
            group_id,
            allocations,
        )
    return jsonify({"reconciliation": view})


@review_routes.post("/api/trips/<trip_name>/reconciliation/coverage-settings")
def update_coverage_settings(trip_name: str):
    data = request.get_json(silent=True) or {}
    expected_accounts = data.get("expected_accounts", [])
    if not isinstance(expected_accounts, list):
        raise ValueError("Expected accounts must be submitted as a list.")
    with jobs.mutation_guard(trip_name):
        coverage = set_coverage_settings(
            resolve_trip(root, trip_name),
            [str(value) for value in expected_accounts],
        )
    return jsonify({"coverage": coverage})
