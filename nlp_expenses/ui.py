from __future__ import annotations

import hmac
import secrets
import shutil
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, jsonify, redirect, request, session
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.serving import make_server

from nlp_expenses.jobs import JobConflictError, JobManager
from nlp_expenses.line_items import (
    ExpenseReviewValidationError,
)
from nlp_expenses.reconciliation import (
    InvoiceValidationError,
)
from nlp_expenses.ui_routes_ops import ops_routes
from nlp_expenses.ui_routes_reviews import review_routes
from nlp_expenses.ui_routes_trips import trip_routes

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(
    root: Path, access_token: str | None = None, job_manager: JobManager | None = None
) -> Flask:
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
    app.register_blueprint(trip_routes)
    app.register_blueprint(review_routes)
    app.register_blueprint(ops_routes)

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
                return jsonify(
                    {
                        "error": "The local session expired. Reload the application from the launcher."
                    }
                ), 403
        return None

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
        return jsonify(
            {"error": "The selected upload is too large. Upload fewer files at a time."}
        ), 413

    return app


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
    candidates = (
        [preferred] if preferred == 0 else list(range(preferred, min(preferred + 20, 65536)))
    )
    for candidate in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return sock.getsockname()[1]
    raise RuntimeError(f"No local port was available near {preferred}.")
