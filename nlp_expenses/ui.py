from __future__ import annotations

import hmac
import os
import secrets
import shutil
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.serving import make_server

from nlp_expenses.accounting import save_trip_accounting_profile
from nlp_expenses.config import load_dotenv, save_openai_settings
from nlp_expenses.consolidation import (
    consolidation_view,
    ensure_consolidation_finalized,
    finalize_consolidation,
)
from nlp_expenses.extraction.text import HEIC_AVAILABLE
from nlp_expenses.generator import SUPPORTED_RECEIPTS
from nlp_expenses.jobs import JobConflictError, JobManager
from nlp_expenses.lifecycle import (
    approve_trip,
    export_approved_package,
    resolve_package,
    set_trip_archived,
)
from nlp_expenses.line_items import (
    ExpenseReviewValidationError,
    add_line_item,
    ensure_line_item_review_ready,
    line_item_review_view,
    remove_line_item,
    reset_receipt_review,
    set_expense_review,
    set_line_item_review,
)
from nlp_expenses.reconciliation import (
    InvoiceValidationError,
    confirm_statement_coverage,
    ensure_reconciliation_ready,
    reconciliation_view,
    set_coverage_settings,
    set_invoice_review,
    set_manual_match,
    set_transaction_allocations,
    set_transaction_decision,
)
from nlp_expenses.statement_normalizer import set_statement_date_convention
from nlp_expenses.trip_metadata import (
    save_policy_exception,
    save_trip_metadata,
    trip_metadata,
    validate_trip_metadata,
)
from nlp_expenses.trips import trip_mode
from nlp_expenses.ui_services import (
    change_trip_claim_program,
    change_trip_mode,
    create_trip,
    delete_trip,
    open_workbook,
    remove_source_file,
    resolve_trip,
    resolve_manifest,
    resolve_workbook,
    reveal_in_finder,
    store_upload,
    trip_details,
    trip_file_state,
    trip_summaries,
)


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(root: Path, access_token: str | None = None, job_manager: JobManager | None = None) -> Flask:
    root = root.resolve()
    token = access_token or secrets.token_urlsafe(24)
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secrets.token_hex(32),
        MAX_CONTENT_LENGTH=512 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        ROOT_PATH=root,
        ACCESS_TOKEN=token,
    )
    jobs = job_manager or JobManager(root)
    app.extensions["generation_jobs"] = jobs

    @app.before_request
    def protect_local_app():
        if request.endpoint == "static":
            return None
        if not session.get("authenticated"):
            supplied = request.args.get("token", "")
            if supplied and hmac.compare_digest(supplied, app.config["ACCESS_TOKEN"]):
                session["authenticated"] = True
                session["csrf_token"] = secrets.token_urlsafe(24)
                clean_args = request.args.to_dict(flat=True)
                clean_args.pop("token", None)
                target = request.path
                if clean_args:
                    target = f"{target}?{urlencode(clean_args)}"
                return redirect(target)
            return jsonify({"error": "Open this page from the NLP Expenses launcher."}), 403
        if request.method in MUTATING_METHODS:
            supplied_csrf = request.headers.get("X-CSRF-Token", "")
            expected_csrf = session.get("csrf_token", "")
            if not supplied_csrf or not hmac.compare_digest(supplied_csrf, expected_csrf):
                return jsonify({"error": "The local session expired. Reload the application from the launcher."}), 403
        return None

    @app.get("/")
    def index():
        show_archived = request.args.get("archived") == "1"
        trips = trip_summaries(root, include_archived=show_archived)
        selected_name = request.args.get("trip")
        names = {item["name"] for item in trips}
        if selected_name not in names:
            selected_name = trips[0]["name"] if trips else None
        selected = trip_details(root, selected_name) if selected_name else None
        generation_job = jobs.latest(selected_name) if selected_name else None
        sync_job = jobs.latest_reconciliation(selected_name) if selected_name else None
        line_item_job = jobs.latest_line_item_review(selected_name) if selected_name else None
        active_job = jobs.active_for_trip(selected_name) if selected_name else None
        selected_trip = resolve_trip(root, selected_name) if selected_name and selected else None
        reconciliation = reconciliation_view(selected_trip) if selected_trip else None
        consolidation = consolidation_view(root, selected_trip) if selected_trip else None
        state = {
            "trips": trips,
            "selected": selected,
            "job": generation_job.to_dict() if generation_job else None,
            "reconciliation_job": sync_job.to_dict() if sync_job else None,
            "line_item_job": line_item_job.to_dict() if line_item_job else None,
            "active_job": active_job.to_dict() if active_job else None,
            "reconciliation": reconciliation,
            "consolidation": consolidation,
            "system": system_status(root),
            "show_archived": show_archived,
        }
        return render_template("index.html", state=state, csrf_token=session["csrf_token"])

    @app.get("/api/trips/<trip_name>")
    def get_trip(trip_name: str):
        details = trip_details(root, trip_name)
        job = jobs.latest(trip_name)
        sync_job = jobs.latest_reconciliation(trip_name)
        line_item_job = jobs.latest_line_item_review(trip_name)
        active_job = jobs.active_for_trip(trip_name)
        selected_trip = resolve_trip(root, trip_name)
        reconciliation = reconciliation_view(selected_trip)
        return jsonify(
            {
                "trip": details,
                "job": job.to_dict() if job else None,
                "reconciliation_job": sync_job.to_dict() if sync_job else None,
                "line_item_job": line_item_job.to_dict() if line_item_job else None,
                "active_job": active_job.to_dict() if active_job else None,
                "reconciliation": reconciliation,
                "consolidation": consolidation_view(root, selected_trip),
                "system": system_status(root),
            }
        )

    @app.get("/api/trips/<trip_name>/file-state")
    def get_trip_file_state(trip_name: str):
        return jsonify(
            {
                "file_state": trip_file_state(root, trip_name),
                "busy": jobs.active_for_trip(trip_name) is not None,
            }
        )

    @app.post("/api/trips")
    def create_trip_route():
        data = request.get_json(silent=True) or {}
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("Trip metadata must be submitted as an object.")
        claim_program = str(data.get("claim_program", "")).lower()
        metadata["claim_program"] = claim_program
        validate_trip_metadata(metadata)
        trip = create_trip(
            root,
            str(data.get("month", "")),
            str(data.get("description", "")),
            claim_program=claim_program,
        )
        if metadata:
            save_trip_metadata(trip, {**trip_metadata(trip), **metadata})
        return jsonify({"trip": trip_details(root, trip.name)}), 201

    @app.post("/api/trips/<trip_name>/mode")
    def update_mode(trip_name: str):
        data = request.get_json(silent=True) or {}
        with jobs.mutation_guard(trip_name):
            change_trip_mode(root, trip_name, str(data.get("mode", "")).lower())
        return jsonify({"trip": trip_details(root, trip_name)})

    @app.post("/api/trips/<trip_name>/claim-program")
    def update_claim_program(trip_name: str):
        data = request.get_json(silent=True) or {}
        with jobs.mutation_guard(trip_name):
            change_trip_claim_program(
                root,
                trip_name,
                str(data.get("claim_program", "")).lower(),
            )
        return jsonify({"trip": trip_details(root, trip_name)})

    @app.post("/api/trips/<trip_name>/upload/<kind>")
    def upload(trip_name: str, kind: str):
        files = request.files.getlist("files")
        if not files:
            raise ValueError("Choose one or more files to upload.")
        results = []
        with jobs.mutation_guard(trip_name):
            for uploaded in files:
                if not uploaded.filename:
                    continue
                results.append(store_upload(root, trip_name, kind, uploaded.filename, uploaded.stream).__dict__)
        if not results:
            raise ValueError("Choose one or more files to upload.")
        return jsonify({"files": results, "trip": trip_details(root, trip_name)})

    @app.post("/api/trips/<trip_name>/remove-file")
    def remove_file(trip_name: str):
        data = request.get_json(silent=True) or {}
        with jobs.mutation_guard(trip_name):
            remove_source_file(root, trip_name, str(data.get("kind", "")), str(data.get("filename", "")))
        return jsonify({"trip": trip_details(root, trip_name)})

    @app.delete("/api/trips/<trip_name>")
    def delete_trip_route(trip_name: str):
        data = request.get_json(silent=True) or {}
        with jobs.mutation_guard(trip_name):
            delete_trip(root, trip_name, str(data.get("confirmation", "")))
        jobs.forget_trip(trip_name)
        return jsonify({"deleted": trip_name})

    @app.post("/api/trips/<trip_name>/statement-date-convention")
    def update_statement_date_convention(trip_name: str):
        data = request.get_json(silent=True) or {}
        with jobs.mutation_guard(trip_name):
            trip = resolve_trip(root, trip_name)
            set_statement_date_convention(
                trip,
                str(data.get("filename", "")),
                str(data.get("convention", "")),
            )
        return jsonify({"trip": trip_details(root, trip_name)})

    @app.post("/api/trips/<trip_name>/accounting-profile")
    def update_accounting_profile(trip_name: str):
        data = request.get_json(silent=True) or {}
        profile = data.get("profile")
        if not isinstance(profile, dict):
            raise ValueError("Accounting profile must be submitted as an object.")
        with jobs.mutation_guard(trip_name):
            save_trip_accounting_profile(
                root,
                resolve_trip(root, trip_name),
                profile,
                make_default=bool(data.get("make_default")),
            )
        return jsonify({"trip": trip_details(root, trip_name)})

    @app.post("/api/trips/<trip_name>/metadata")
    def update_trip_metadata(trip_name: str):
        data = request.get_json(silent=True) or {}
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("Trip metadata must be submitted as an object.")
        with jobs.mutation_guard(trip_name):
            claim_program = str(metadata.get("claim_program") or "")
            if claim_program:
                change_trip_claim_program(root, trip_name, claim_program)
                if claim_program == "ivado_sponsored" and not metadata.get("sponsor"):
                    metadata["sponsor"] = "IVADO Labs"
            save_trip_metadata(resolve_trip(root, trip_name), metadata)
        return jsonify(
            {
                "trip": trip_details(root, trip_name),
                "reconciliation": reconciliation_view(resolve_trip(root, trip_name)),
            }
        )

    @app.post("/api/trips/<trip_name>/policy-exception")
    def update_policy_exception(trip_name: str):
        data = request.get_json(silent=True) or {}
        with jobs.mutation_guard(trip_name):
            trip = resolve_trip(root, trip_name)
            save_policy_exception(
                trip,
                str(data.get("warning_id", "")),
                str(data.get("note", "")),
            )
        return jsonify(
            {
                "trip": trip_details(root, trip_name),
                "reconciliation": reconciliation_view(resolve_trip(root, trip_name)),
            }
        )

    @app.post("/api/trips/<trip_name>/approve")
    def approve(trip_name: str):
        data = request.get_json(silent=True) or {}
        with jobs.mutation_guard(trip_name):
            trip = resolve_trip(root, trip_name)
            approval = approve_trip(
                root,
                trip,
                str(data.get("workbook", "")),
                str(data.get("reviewer", "")),
                str(data.get("note", "")),
            )
        return jsonify({"approval": approval, "trip": trip_details(root, trip_name)})

    @app.post("/api/trips/<trip_name>/export-package")
    def export_package(trip_name: str):
        with jobs.mutation_guard(trip_name):
            trip = resolve_trip(root, trip_name)
            package = export_approved_package(root, trip)
        return jsonify(
            {
                "package": {"name": package.name, "size": package.stat().st_size},
                "trip": trip_details(root, trip_name),
            }
        )

    @app.post("/api/trips/<trip_name>/archive")
    def archive_trip(trip_name: str):
        data = request.get_json(silent=True) or {}
        with jobs.mutation_guard(trip_name):
            set_trip_archived(resolve_trip(root, trip_name), bool(data.get("archived")))
        return jsonify({"trip": trip_details(root, trip_name)})

    @app.post("/api/trips/<trip_name>/generate")
    def generate(trip_name: str):
        data = request.get_json(silent=True) or {}
        statements_complete = bool(data.get("statements_complete"))
        details = trip_details(root, trip_name)
        trip = resolve_trip(root, trip_name)
        if not details["receipts"]:
            raise ValueError("Add at least one receipt before generating the workbook.")
        if details["mode"] == "arvine":
            if details["statement_errors"]:
                raise ValueError("Fix the statement validation errors before generating the workbook.")
        ensure_reconciliation_ready(trip)
        ensure_consolidation_finalized(root, trip)
        if details["mode"] == "arvine":
            # Statement completeness was confirmed as part of the finalized app review.
            statements_complete = True
        quality = receipt_quality(ensure_line_item_review_ready(trip))
        if quality == "best" and not system_status(root)["openai_configured"]:
            raise ValueError("The receipt scan used Best quality. Restore the OpenAI API key before generating.")
        job = jobs.start(trip_name, quality, statements_complete)
        return jsonify({"job": job.to_dict()}), 202

    @app.post("/api/trips/<trip_name>/reconcile")
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
            raise ValueError("The receipt scan used Best quality. Restore the OpenAI API key before reconciling.")
        job = jobs.start_reconciliation(trip_name, quality)
        return jsonify({"job": job.to_dict()}), 202

    @app.post("/api/trips/<trip_name>/line-items/sync")
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

    @app.post("/api/trips/<trip_name>/line-items/item")
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

    @app.post("/api/trips/<trip_name>/line-items/expense")
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

    @app.post("/api/trips/<trip_name>/line-items/add")
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

    @app.post("/api/trips/<trip_name>/line-items/remove")
    def remove_receipt_line(trip_name: str):
        data = request.get_json(silent=True) or {}
        with jobs.mutation_guard(trip_name):
            view = remove_line_item(
                resolve_trip(root, trip_name),
                str(data.get("source_file", "")),
                str(data.get("line_id", "")),
            )
        return jsonify({"line_item_review": view})

    @app.post("/api/trips/<trip_name>/line-items/reset")
    def reset_line_items(trip_name: str):
        data = request.get_json(silent=True) or {}
        with jobs.mutation_guard(trip_name):
            view = reset_receipt_review(
                resolve_trip(root, trip_name),
                str(data.get("source_file", "")),
            )
        return jsonify({"line_item_review": view, "trip": trip_details(root, trip_name)})

    @app.get("/api/trips/<trip_name>/reconciliation")
    def get_reconciliation(trip_name: str):
        trip = resolve_trip(root, trip_name)
        return jsonify({"reconciliation": reconciliation_view(trip)})

    @app.get("/api/trips/<trip_name>/consolidation")
    def get_consolidation(trip_name: str):
        return jsonify({"consolidation": consolidation_view(root, resolve_trip(root, trip_name))})

    @app.post("/api/trips/<trip_name>/finalize")
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

    @app.post("/api/trips/<trip_name>/reconciliation/mapping")
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

    @app.post("/api/trips/<trip_name>/reconciliation/invoice")
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

    @app.post("/api/trips/<trip_name>/reconciliation/transaction-decision")
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

    @app.post("/api/trips/<trip_name>/reconciliation/allocations")
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

    @app.post("/api/trips/<trip_name>/reconciliation/coverage-settings")
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

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id: str):
        return jsonify({"job": jobs.get(job_id).to_dict()})

    @app.post("/api/settings/openai")
    def configure_openai():
        data = request.get_json(silent=True) or {}
        key = str(data.get("api_key", "")).strip()
        if not key:
            raise ValueError("Enter an OpenAI API key.")
        existing = load_dotenv(root)
        model = existing.get("OPENAI_MODEL", os.getenv("OPENAI_MODEL", "gpt-5.2"))
        save_openai_settings(root, key, model, existing)
        return jsonify({"system": system_status(root)})

    @app.post("/api/trips/<trip_name>/reveal")
    def reveal(trip_name: str):
        data = request.get_json(silent=True) or {}
        path = reveal_in_finder(root, trip_name, str(data.get("target", "")), data.get("filename"))
        return jsonify({"revealed": path.name})

    @app.post("/api/trips/<trip_name>/open-workbook")
    def open_workbook_route(trip_name: str):
        data = request.get_json(silent=True) or {}
        path = open_workbook(root, trip_name, str(data.get("filename", "")))
        return jsonify({"opened": path.name})

    @app.get("/api/trips/<trip_name>/download-workbook")
    def download_workbook(trip_name: str):
        path = resolve_workbook(root, trip_name, request.args.get("filename", ""))
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.get("/api/trips/<trip_name>/download-manifest")
    def download_manifest(trip_name: str):
        path = resolve_manifest(root, trip_name, request.args.get("filename", ""))
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.get("/api/trips/<trip_name>/download-package")
    def download_package(trip_name: str):
        trip = resolve_trip(root, trip_name)
        path = resolve_package(trip, request.args.get("filename", ""))
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.errorhandler(JobConflictError)
    def job_conflict(error):
        return jsonify({"error": str(error)}), 409

    @app.errorhandler(InvoiceValidationError)
    def invoice_validation(error):
        return jsonify({"error": str(error), "fields": error.fields}), 400

    @app.errorhandler(ExpenseReviewValidationError)
    def expense_validation(error):
        return jsonify({"error": str(error), "fields": error.fields}), 400

    @app.errorhandler(FileExistsError)
    def already_exists(error):
        return jsonify({"error": str(error)}), 409

    @app.errorhandler(FileNotFoundError)
    def not_found(error):
        return jsonify({"error": str(error)}), 404

    @app.errorhandler(ValueError)
    def bad_request(error):
        return jsonify({"error": str(error)}), 400

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify({"error": "The selected upload is too large. Upload fewer files at a time."}), 413

    return app


def system_status(root: Path) -> dict:
    configured = bool(load_dotenv(root).get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"))
    return {
        "openai_configured": configured,
        "openai_env_path": str((root / ".env").resolve()),
        "tesseract_available": bool(shutil.which("tesseract")),
        "heic_available": HEIC_AVAILABLE,
        "receipt_extensions": sorted(SUPPORTED_RECEIPTS),
        "receipt_accept": ",".join(sorted(SUPPORTED_RECEIPTS)),
        "arvine_statement_extensions": sorted({".csv", ".xls", ".xlsx"}),
        "ivado_statement_extensions": sorted({".csv", ".pdf", ".xls", ".xlsx"}),
    }


def receipt_quality(review: dict) -> str:
    """Return the single extraction method recorded by the receipt scan."""

    quality = str(review.get("quality", "basic")).lower()
    if quality not in {"basic", "best"}:
        raise ValueError("The saved receipt extraction method is invalid. Rescan the receipts.")
    return quality


def run_local_ui(root: Path, port: int = 8765, open_browser: bool = True) -> None:
    selected_port = available_port(port)
    access_token = secrets.token_urlsafe(24)
    app = create_app(root, access_token=access_token)
    server = make_server("127.0.0.1", selected_port, app, threaded=True)
    url = f"http://127.0.0.1:{selected_port}/?token={access_token}"
    print(f"NLP Expenses is running at {url}")
    print("Close this window or press Control-C to stop it.")
    if open_browser:
        open_local_url(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nNLP Expenses stopped.")
    finally:
        server.shutdown()


def open_local_url(url: str) -> None:
    """Open the authenticated localhost URL using the native Mac launcher."""

    if sys.platform == "darwin" and shutil.which("open"):
        subprocess.run(["open", url], check=False)
        return
    webbrowser.open(url)


def available_port(preferred: int) -> int:
    if preferred < 0 or preferred > 65535:
        raise ValueError("Port must be between 0 and 65535.")
    candidates = [preferred] if preferred == 0 else list(range(preferred, min(preferred + 20, 65536)))
    for candidate in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return sock.getsockname()[1]
    raise RuntimeError(f"No local port was available near {preferred}.")
