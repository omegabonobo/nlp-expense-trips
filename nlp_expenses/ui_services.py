from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from werkzeug.utils import secure_filename

from nlp_expenses.accounting import trip_accounting_profile
from nlp_expenses.extraction.text import validate_receipt_content
from nlp_expenses.generator import SUPPORTED_RECEIPTS
from nlp_expenses.lifecycle import list_packages, trip_lifecycle
from nlp_expenses.line_items import (
    line_item_review_view,
    persist_review_after_receipt_removal,
    receipt_scan_status,
)
from nlp_expenses.statement_normalizer import preflight_statement_files
from nlp_expenses.trip_manifest import CONTRACT_FILENAME
from nlp_expenses.trip_metadata import (
    CLAIM_PROGRAMS,
    normalize_claim_program,
    required_metadata_gaps,
    save_trip_metadata,
    trip_metadata,
)
from nlp_expenses.trips import (
    TRIP_MODES,
    ensure_trip,
    list_receipt_files,
    list_trips,
    load_trip_config,
    normalize_trip_mode,
    relative_source_name,
    save_trip_mode,
    trip_mode,
    trip_receipts_dir,
    trip_statements_dir,
    validate_source_name,
    validate_trip_name,
)

COMPANY_STATEMENTS = {".csv", ".xls", ".xlsx"}
IVADO_STATEMENTS = COMPANY_STATEMENTS | {".pdf"}
FILE_KINDS = {"receipts", "statements"}


@dataclass(frozen=True)
class UploadResult:
    name: str
    status: str


def create_trip_name(month: str, description: str) -> str:
    compact_month = month.replace("-", "").strip()
    if not re.fullmatch(r"20\d{2}(?:0[1-9]|1[0-2])", compact_month):
        raise ValueError("Choose a valid trip month.")
    normalized = (
        unicodedata.normalize("NFKD", description).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if not slug:
        raise ValueError("Enter a descriptive trip name.")
    name = f"{compact_month}_{slug}"
    if not validate_trip_name(name):
        raise ValueError("The trip name could not be converted to a valid folder name.")
    return name


def create_trip(
    root: Path,
    month: str,
    description: str,
    mode: str = "company",
    claim_program: str | None = None,
) -> Path:
    claim_program = normalize_claim_program(claim_program) if claim_program is not None else None
    mode = normalize_trip_mode(mode)
    if claim_program is not None and claim_program not in CLAIM_PROGRAMS:
        raise ValueError("Choose own-company reimbursement or IVADO-reimbursed trip.")
    if claim_program:
        mode = "ivado" if claim_program == "ivado_reimbursed" else "company"
    if mode not in TRIP_MODES:
        raise ValueError("Choose either own-company or IVADO reimbursement.")
    name = create_trip_name(month, description)
    trip = root.resolve() / "trips" / name
    if trip.exists():
        raise FileExistsError(f"A trip named {name} already exists.")
    trip = ensure_trip(root.resolve(), name, mode=mode)
    if claim_program:
        metadata = trip_metadata(trip)
        metadata["claim_program"] = claim_program
        if claim_program == "ivado_reimbursed" and not metadata["sponsor"]:
            metadata["sponsor"] = "IVADO Labs"
        save_trip_metadata(trip, metadata)
    return trip


def resolve_trip(root: Path, name: str) -> Path:
    if not validate_trip_name(name):
        raise ValueError("Invalid trip name.")
    trips_root = (root.resolve() / "trips").resolve()
    candidate = (trips_root / name).resolve()
    if candidate.parent != trips_root:
        raise ValueError("Trip path is outside the trips folder.")
    if not candidate.is_dir():
        raise FileNotFoundError(f"Trip {name} was not found.")
    return candidate


def delete_trip(root: Path, name: str, confirmation: str) -> None:
    """Permanently remove one validated trip folder and all data below it."""

    if confirmation != name:
        raise ValueError("Type the exact trip identifier to confirm deletion.")
    trip = resolve_trip(root, name)
    shutil.rmtree(trip)


def change_trip_mode(root: Path, name: str, mode: str) -> None:
    mode = normalize_trip_mode(mode)
    if mode not in TRIP_MODES:
        raise ValueError("Choose either own-company or IVADO reimbursement.")
    save_trip_mode(resolve_trip(root, name), mode)


def change_trip_claim_program(root: Path, name: str, claim_program: str) -> None:
    claim_program = normalize_claim_program(claim_program)
    if claim_program not in CLAIM_PROGRAMS:
        raise ValueError("Choose own-company reimbursement or IVADO-reimbursed trip.")
    trip = resolve_trip(root, name)
    metadata = trip_metadata(trip)
    metadata["claim_program"] = claim_program
    if claim_program == "ivado_reimbursed" and not metadata["sponsor"]:
        metadata["sponsor"] = "IVADO Labs"
    save_trip_metadata(trip, metadata)
    save_trip_mode(trip, "ivado" if claim_program == "ivado_reimbursed" else "company")


def trip_summaries(root: Path, include_archived: bool = False) -> list[dict]:
    summaries = []
    for trip in list_trips(root.resolve()):
        archived = bool(load_trip_config(trip).get("archived"))
        if archived and not include_archived:
            continue
        summaries.append(
            {
                "name": trip.name,
                "label": trip_label(trip.name),
                "mode": trip_mode(trip),
                "claim_program": trip_metadata(trip).get("claim_program") or "",
                "archived": archived,
                "receipt_count": len(list_receipt_files(trip_receipts_dir(trip))),
                "statement_count": len(list_source_files(trip_statements_dir(trip))),
                "modified": trip_last_modified(trip),
            }
        )
    return sorted(summaries, key=lambda item: (item["modified"], item["name"]), reverse=True)


def trip_details(root: Path, name: str) -> dict:
    trip = resolve_trip(root, name)
    from nlp_expenses.reconciliation import repair_reconciliation_after_missing_receipts

    repair_reconciliation_after_missing_receipts(trip)
    selected_mode = trip_mode(trip)
    receipts_folder = trip_receipts_dir(trip)
    receipts = list_receipt_files(receipts_folder)
    scan_status = receipt_scan_status(trip)
    scan_by_source = {item["source_file"]: item for item in scan_status["receipts"]}
    statements = list_source_files(trip_statements_dir(trip))
    statement_reports: dict[str, dict] = {}
    if selected_mode == "company":
        for report in preflight_statement_files(statements):
            statement_reports[report.source_file.name] = {
                "provider": report.provider,
                "rows_read": report.rows_read,
                "rows_normalized": report.rows_normalized,
                "date_convention": report.date_convention,
                "date_convention_required": report.date_convention_required,
                "date_samples": list(report.date_samples),
                "warnings": list(report.warnings),
                "errors": list(report.errors),
            }
    return {
        "name": trip.name,
        "label": trip_label(trip.name),
        "mode": selected_mode,
        "claim_program": trip_metadata(trip).get("claim_program") or "",
        "metadata": trip_metadata(trip),
        "metadata_gaps": required_metadata_gaps(trip),
        "accounting_profile": trip_accounting_profile(root, trip),
        "path": str(trip),
        "receipts_path": str(trip_receipts_dir(trip)),
        "statements_path": str(trip_statements_dir(trip)),
        "file_state": source_file_state(receipts, statements),
        "receipts": [
            {
                **file_details(path, receipts_folder),
                "scan": scan_by_source.get(
                    relative_source_name(receipts_folder, path),
                    {"status": "not_scanned", "quality": None, "scanned_at": None},
                ),
            }
            for path in receipts
        ],
        "receipt_scan": scan_status,
        "statements": [
            {**file_details(path), "validation": statement_reports.get(path.name)}
            for path in statements
        ],
        "workbooks": [file_details(path) for path in list_workbooks(trip)],
        "manifests": [file_details(path) for path in list_manifests(trip)],
        "packages": [file_details(path) for path in list_packages(trip)],
        "statement_errors": [
            error for report in statement_reports.values() for error in report["errors"]
        ],
        "line_item_review": line_item_review_view(trip),
        "lifecycle": trip_lifecycle(root, trip),
    }


def trip_file_state(root: Path, name: str) -> dict:
    trip = resolve_trip(root, name)
    return source_file_state(
        list_receipt_files(trip_receipts_dir(trip)),
        list_source_files(trip_statements_dir(trip)),
    )


def source_file_state(receipts: list[Path], statements: list[Path]) -> dict:
    """Return a cheap signature for detecting Finder-side source changes."""

    entries: list[str] = []
    counts = {"receipts": 0, "statements": 0}
    for kind, paths in (("receipts", receipts), ("statements", statements)):
        for path in paths:
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            counts[kind] += 1
            entries.append(
                f"{kind}\0{path.resolve().as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}"
            )
    digest = hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()
    return {"signature": digest, **counts}


def allowed_extensions(mode: str, kind: str) -> set[str]:
    if kind == "receipts":
        return SUPPORTED_RECEIPTS
    if kind != "statements":
        raise ValueError("Unknown upload type.")
    return COMPANY_STATEMENTS if mode == "company" else IVADO_STATEMENTS


def store_upload(
    root: Path, trip_name: str, kind: str, filename: str, stream: BinaryIO
) -> UploadResult:
    trip = resolve_trip(root, trip_name)
    destination = source_folder(trip, kind)
    cleaned = secure_filename(Path(filename).name)
    if not cleaned or cleaned.startswith("."):
        raise ValueError("The uploaded file needs a valid filename.")
    extension = Path(cleaned).suffix.lower()
    if extension not in allowed_extensions(trip_mode(trip), kind):
        formats = ", ".join(
            sorted(ext.lstrip(".").upper() for ext in allowed_extensions(trip_mode(trip), kind))
        )
        raise ValueError(f"{cleaned} is not supported here. Use {formats}.")

    destination.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=destination)
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as handle:
            shutil.copyfileobj(stream, handle, length=1024 * 1024)
        if kind == "receipts":
            validation_path = temporary.with_suffix(extension)
            os.replace(temporary, validation_path)
            temporary = validation_path
            validate_receipt_content(temporary)
        incoming_hash = file_hash(temporary)
        existing_files = (
            list_receipt_files(destination)
            if kind == "receipts"
            else list_source_files(destination)
        )
        for existing in existing_files:
            if (
                existing.stat().st_size == temporary.stat().st_size
                and file_hash(existing) == incoming_hash
            ):
                existing_name = (
                    relative_source_name(destination, existing)
                    if kind == "receipts"
                    else existing.name
                )
                return UploadResult(name=existing_name, status="duplicate")

        target = unique_destination(destination, cleaned)
        os.replace(temporary, target)
        return UploadResult(name=target.name, status="uploaded")
    finally:
        temporary.unlink(missing_ok=True)


def remove_source_file(root: Path, trip_name: str, kind: str, filename: str) -> None:
    trip = resolve_trip(root, trip_name)
    folder = source_folder(trip, kind).resolve()
    target = (
        safe_source_child(folder, filename) if kind == "receipts" else safe_child(folder, filename)
    )
    if not target.is_file():
        raise FileNotFoundError(f"{filename} was not found.")
    target.unlink()
    if kind == "receipts":
        persist_review_after_receipt_removal(trip)
        from nlp_expenses.reconciliation import persist_reconciliation_after_receipt_removal

        persist_reconciliation_after_receipt_removal(trip, filename)


def resolve_receipt(root: Path, trip_name: str, filename: str) -> Path:
    """Resolve one receipt, including a nested receipt path, without escaping the trip."""

    trip = resolve_trip(root, trip_name)
    target = safe_source_child(trip_receipts_dir(trip), filename)
    if not target.is_file() or target.suffix.lower() not in SUPPORTED_RECEIPTS:
        raise FileNotFoundError("The receipt file was not found.")
    return target


def versioned_output_path(trip: Path, mode: str, now: datetime | None = None) -> Path:
    mode = normalize_trip_mode(mode)
    if mode not in TRIP_MODES:
        raise ValueError("Unknown trip mode.")
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = f"expense_review_{trip.name}_{mode}_{timestamp}"
    candidate = trip / f"{base}.xlsx"
    counter = 2
    while candidate.exists():
        candidate = trip / f"{base}-{counter}.xlsx"
        counter += 1
    return candidate


def list_workbooks(trip: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in trip.iterdir()
            if path.is_file()
            and path.name.startswith("expense_review_")
            and path.suffix.lower() == ".xlsx"
            and not path.name.startswith("~$")
        ),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )


def list_manifests(trip: Path) -> list[Path]:
    path = trip / CONTRACT_FILENAME
    return [path] if path.is_file() else []


def resolve_manifest(root: Path, trip_name: str, filename: str) -> Path:
    trip = resolve_trip(root, trip_name)
    target = safe_child(trip, filename)
    if target not in list_manifests(trip):
        raise FileNotFoundError("Reimbursement manifest was not found in the selected trip.")
    return target


def resolve_workbook(root: Path, trip_name: str, filename: str) -> Path:
    trip = resolve_trip(root, trip_name)
    target = safe_child(trip, filename)
    if target not in list_workbooks(trip):
        raise FileNotFoundError("Workbook was not found in the selected trip.")
    return target


def reveal_in_finder(root: Path, trip_name: str, target: str, filename: str | None = None) -> Path:
    trip = resolve_trip(root, trip_name)
    targets = {
        "trip": trip,
        "receipts": trip_receipts_dir(trip),
        "statements": trip_statements_dir(trip),
    }
    if target == "workbook" and filename:
        path = resolve_workbook(root, trip_name, filename)
    elif target in targets:
        path = targets[target]
    else:
        raise ValueError("Unknown Finder target.")
    command = ["open", "-R", str(path)] if path.is_file() else ["open", str(path)]
    subprocess.run(command, check=False)
    return path


def open_workbook(root: Path, trip_name: str, filename: str) -> Path:
    workbook = resolve_workbook(root, trip_name, filename)
    subprocess.run(["open", str(workbook)], check=False)
    return workbook


def trip_label(name: str) -> str:
    month, _, slug = name.partition("_")
    month_label = f"{month[:4]}-{month[4:]}" if len(month) == 6 else month
    return f"{slug.replace('-', ' ').replace('_', ' ').title()} · {month_label}"


def file_details(path: Path, source_root: Path | None = None) -> dict:
    stat = path.stat()
    return {
        "name": relative_source_name(source_root, path) if source_root else path.name,
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def trip_last_modified(trip: Path) -> str:
    """Return the latest modification time anywhere inside a trip folder."""

    latest = trip.stat().st_mtime
    for path in trip.rglob("*"):
        try:
            latest = max(latest, path.stat().st_mtime)
        except FileNotFoundError:
            continue
    return datetime.fromtimestamp(latest).isoformat(timespec="seconds")


def list_source_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(
        path for path in folder.iterdir() if path.is_file() and not path.name.startswith(".")
    )


def source_folder(trip: Path, kind: str) -> Path:
    if kind == "receipts":
        return trip_receipts_dir(trip)
    if kind == "statements":
        return trip_statements_dir(trip)
    raise ValueError("Unknown file type.")


def safe_child(folder: Path, filename: str) -> Path:
    if not filename or filename != Path(filename).name:
        raise ValueError("Invalid filename.")
    target = (folder / filename).resolve()
    if target.parent != folder.resolve():
        raise ValueError("File path is outside the selected trip folder.")
    return target


def safe_source_child(folder: Path, relative_name: str) -> Path:
    normalized = validate_source_name(relative_name)
    target = (folder / normalized).resolve()
    try:
        target.relative_to(folder.resolve())
    except ValueError as exc:
        raise ValueError("File path is outside the selected trip folder.") from exc
    return target


def unique_destination(folder: Path, filename: str) -> Path:
    path = folder / filename
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    counter = 2
    while True:
        candidate = folder / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
