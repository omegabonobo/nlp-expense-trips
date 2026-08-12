from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file
from werkzeug.local import LocalProxy

from nlp_expenses.config import load_dotenv, save_openai_settings
from nlp_expenses.jobs import JobManager
from nlp_expenses.lifecycle import (
    resolve_package,
)
from nlp_expenses.ui_services import (
    open_workbook,
    resolve_manifest,
    resolve_receipt,
    resolve_trip,
    resolve_workbook,
    reveal_in_finder,
)
from nlp_expenses.ui_status import system_status

root: Path = LocalProxy(lambda: current_app.config["ROOT_PATH"])
jobs: JobManager = LocalProxy(lambda: current_app.extensions["generation_jobs"])

ops_routes = Blueprint("ops", __name__)


@ops_routes.get("/api/jobs/<job_id>")
def job_status(job_id: str):
    return jsonify({"job": jobs.get(job_id).to_dict()})


@ops_routes.post("/api/settings/openai")
def configure_openai():
    data = request.get_json(silent=True) or {}
    key = str(data.get("api_key", "")).strip()
    if not key:
        raise ValueError("Enter an OpenAI API key.")
    existing = load_dotenv(root)
    model = existing.get("OPENAI_MODEL", os.getenv("OPENAI_MODEL", "gpt-5.2"))
    save_openai_settings(root, key, model, existing)
    return jsonify({"system": system_status(root)})


@ops_routes.post("/api/trips/<trip_name>/reveal")
def reveal(trip_name: str):
    data = request.get_json(silent=True) or {}
    path = reveal_in_finder(root, trip_name, str(data.get("target", "")), data.get("filename"))
    return jsonify({"revealed": path.name})


@ops_routes.post("/api/trips/<trip_name>/open-workbook")
def open_workbook_route(trip_name: str):
    data = request.get_json(silent=True) or {}
    path = open_workbook(root, trip_name, str(data.get("filename", "")))
    return jsonify({"opened": path.name})


@ops_routes.get("/api/trips/<trip_name>/download-workbook")
def download_workbook(trip_name: str):
    path = resolve_workbook(root, trip_name, request.args.get("filename", ""))
    return send_file(path, as_attachment=True, download_name=path.name)


@ops_routes.get("/api/trips/<trip_name>/receipt")
def view_receipt(trip_name: str):
    path = resolve_receipt(root, trip_name, request.args.get("filename", ""))
    return send_file(path, as_attachment=False, download_name=path.name, conditional=True)


@ops_routes.get("/api/trips/<trip_name>/download-manifest")
def download_manifest(trip_name: str):
    path = resolve_manifest(root, trip_name, request.args.get("filename", ""))
    return send_file(path, as_attachment=True, download_name=path.name)


@ops_routes.get("/api/trips/<trip_name>/download-package")
def download_package(trip_name: str):
    trip = resolve_trip(root, trip_name)
    path = resolve_package(trip, request.args.get("filename", ""))
    return send_file(path, as_attachment=True, download_name=path.name)
