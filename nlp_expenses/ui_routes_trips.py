from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request, session
from werkzeug.local import LocalProxy

from nlp_expenses.accounting import save_trip_accounting_profile
from nlp_expenses.consolidation import (
    consolidation_view,
)
from nlp_expenses.jobs import JobManager
from nlp_expenses.lifecycle import (
    approve_trip,
    export_approved_package,
    set_trip_archived,
)
from nlp_expenses.reconciliation import (
    reconciliation_view,
)
from nlp_expenses.statement_normalizer import set_statement_date_convention
from nlp_expenses.trip_metadata import (
    save_policy_exception,
    save_trip_metadata,
    trip_metadata,
    validate_trip_metadata,
)
from nlp_expenses.ui_services import (
    change_trip_claim_program,
    change_trip_mode,
    create_trip,
    delete_trip,
    remove_source_file,
    resolve_trip,
    store_upload,
    trip_details,
    trip_file_state,
    trip_summaries,
)
from nlp_expenses.ui_status import system_status

root: Path = LocalProxy(lambda: current_app.config["ROOT_PATH"])
jobs: JobManager = LocalProxy(lambda: current_app.extensions["generation_jobs"])

trip_routes = Blueprint("trip", __name__)


@trip_routes.get("/")
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


@trip_routes.get("/api/trips/<trip_name>")
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


@trip_routes.get("/api/trips/<trip_name>/file-state")
def get_trip_file_state(trip_name: str):
    return jsonify(
        {
            "file_state": trip_file_state(root, trip_name),
            "busy": jobs.active_for_trip(trip_name) is not None,
        }
    )


@trip_routes.post("/api/trips")
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


@trip_routes.post("/api/trips/<trip_name>/mode")
def update_mode(trip_name: str):
    data = request.get_json(silent=True) or {}
    with jobs.mutation_guard(trip_name):
        change_trip_mode(root, trip_name, str(data.get("mode", "")).lower())
    return jsonify({"trip": trip_details(root, trip_name)})


@trip_routes.post("/api/trips/<trip_name>/claim-program")
def update_claim_program(trip_name: str):
    data = request.get_json(silent=True) or {}
    with jobs.mutation_guard(trip_name):
        change_trip_claim_program(
            root,
            trip_name,
            str(data.get("claim_program", "")).lower(),
        )
    return jsonify({"trip": trip_details(root, trip_name)})


@trip_routes.post("/api/trips/<trip_name>/upload/<kind>")
def upload(trip_name: str, kind: str):
    files = request.files.getlist("files")
    if not files:
        raise ValueError("Choose one or more files to upload.")
    results = []
    with jobs.mutation_guard(trip_name):
        for uploaded in files:
            if not uploaded.filename:
                continue
            results.append(
                store_upload(root, trip_name, kind, uploaded.filename, uploaded.stream).__dict__
            )
    if not results:
        raise ValueError("Choose one or more files to upload.")
    return jsonify({"files": results, "trip": trip_details(root, trip_name)})


@trip_routes.post("/api/trips/<trip_name>/remove-file")
def remove_file(trip_name: str):
    data = request.get_json(silent=True) or {}
    with jobs.mutation_guard(trip_name):
        remove_source_file(
            root, trip_name, str(data.get("kind", "")), str(data.get("filename", ""))
        )
    return jsonify({"trip": trip_details(root, trip_name)})


@trip_routes.delete("/api/trips/<trip_name>")
def delete_trip_route(trip_name: str):
    data = request.get_json(silent=True) or {}
    with jobs.mutation_guard(trip_name):
        delete_trip(root, trip_name, str(data.get("confirmation", "")))
    jobs.forget_trip(trip_name)
    return jsonify({"deleted": trip_name})


@trip_routes.post("/api/trips/<trip_name>/statement-date-convention")
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


@trip_routes.post("/api/trips/<trip_name>/accounting-profile")
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


@trip_routes.post("/api/trips/<trip_name>/metadata")
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


@trip_routes.post("/api/trips/<trip_name>/policy-exception")
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


@trip_routes.post("/api/trips/<trip_name>/approve")
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


@trip_routes.post("/api/trips/<trip_name>/export-package")
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


@trip_routes.post("/api/trips/<trip_name>/archive")
def archive_trip(trip_name: str):
    data = request.get_json(silent=True) or {}
    with jobs.mutation_guard(trip_name):
        set_trip_archived(resolve_trip(root, trip_name), bool(data.get("archived")))
    return jsonify({"trip": trip_details(root, trip_name)})
