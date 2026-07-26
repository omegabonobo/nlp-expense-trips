from __future__ import annotations

import hashlib
import json
import os
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from nlp_expenses.accounting import trip_accounting_profile
from nlp_expenses.trip_metadata import required_metadata_gaps, trip_metadata
from nlp_expenses.trips import (
    load_trip_config,
    save_trip_config,
    trip_mode,
    trip_receipts_dir,
    trip_statements_dir,
)


APPROVAL_RECORD_PREFIX = ".nlp-expenses-approval-"
PACKAGE_PREFIX = "trip_package_"
RECONCILIATION_FILE = ".nlp-expenses-reconciliation.json"
LINE_ITEM_REVIEW_FILE = ".nlp-expenses-line-items.json"
STATEMENT_SETTINGS_FILE = ".nlp-expenses-statement-settings.json"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def review_input_snapshot(root: Path, trip_dir: Path) -> dict:
    artifacts = []
    for kind, folder in (
        ("receipt", trip_receipts_dir(trip_dir)),
        ("statement", trip_statements_dir(trip_dir)),
    ):
        if not folder.exists():
            continue
        for path in sorted(item for item in folder.iterdir() if item.is_file() and not item.name.startswith(".")):
            artifacts.append(artifact_entry(trip_dir, path, kind))
    for filename, kind in (
        (RECONCILIATION_FILE, "reconciliation"),
        (LINE_ITEM_REVIEW_FILE, "line_item_review"),
        (STATEMENT_SETTINGS_FILE, "statement_settings"),
    ):
        path = trip_dir / filename
        if path.is_file():
            artifacts.append(artifact_entry(trip_dir, path, kind))
    profile = trip_accounting_profile(root, trip_dir)
    metadata = trip_metadata(trip_dir)
    snapshot = {
        "mode": trip_mode(trip_dir),
        "artifacts": artifacts,
        "accounting_profile_sha256": json_sha256(profile),
        "trip_metadata_sha256": json_sha256(metadata),
    }
    snapshot["fingerprint"] = json_sha256(snapshot)
    return snapshot


def record_generated_workbook(root: Path, trip_dir: Path, workbook: Path) -> dict:
    return record_generated_bundle(root, trip_dir, workbook, [workbook])


def record_generated_bundle(
    root: Path,
    trip_dir: Path,
    primary_workbook: Path,
    artifacts: list[Path],
) -> dict:
    """Record every generated artifact that must stay synchronized for approval."""

    primary_workbook = primary_workbook.resolve()
    if primary_workbook.parent != trip_dir.resolve() or not primary_workbook.is_file():
        raise ValueError("Generated workbook must be inside the selected trip.")
    resolved_artifacts: list[Path] = []
    for path in artifacts:
        resolved = path.resolve()
        if resolved.parent != trip_dir.resolve() or not resolved.is_file():
            raise ValueError("Generated artifacts must be files inside the selected trip.")
        if resolved not in resolved_artifacts:
            resolved_artifacts.append(resolved)
    if primary_workbook not in resolved_artifacts:
        resolved_artifacts.insert(0, primary_workbook)

    snapshot = review_input_snapshot(root, trip_dir)
    record = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_fingerprint": snapshot["fingerprint"],
        "workbook_sha256_at_generation": file_sha256(primary_workbook),
        "artifacts": [
            artifact_entry(trip_dir, path, generated_artifact_kind(path, primary_workbook))
            for path in resolved_artifacts
        ],
    }
    config = load_trip_config(trip_dir)
    generated = config.get("generated_workbooks", {})
    if not isinstance(generated, dict):
        generated = {}
    generated[primary_workbook.name] = record
    config["generated_workbooks"] = generated
    save_trip_config(trip_dir, config)
    return record


def approve_trip(
    root: Path,
    trip_dir: Path,
    workbook_name: str,
    reviewer: str,
    note: str,
) -> dict:
    reviewer = " ".join(reviewer.split())
    note = note.strip()
    if not reviewer:
        raise ValueError("Enter the reviewer or approver name.")
    if not note:
        raise ValueError("Add an approval note.")
    gaps = required_metadata_gaps(trip_dir)
    if gaps:
        raise ValueError(f"Complete the trip metadata before approval: {', '.join(gaps)}.")
    workbook = resolve_workbook_for_lifecycle(trip_dir, workbook_name)
    current_inputs = review_input_snapshot(root, trip_dir)
    config = load_trip_config(trip_dir)
    generation = config.get("generated_workbooks", {}).get(workbook.name, {})
    if generation.get("input_fingerprint") != current_inputs["fingerprint"]:
        raise ValueError("Generate a new workbook from the current trip files and review decisions before approval.")

    reconciliation_summary = approval_reconciliation_check(trip_dir)
    approved_at = datetime.now().isoformat(timespec="seconds")
    approval_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    generated_artifacts = generation_artifacts(trip_dir, workbook, generation)
    manifest = {
        "manifest_version": 2,
        "trip": trip_dir.name,
        "approval_id": approval_id,
        "approved_at": approved_at,
        "reviewer": reviewer,
        "note": note,
        "workbook": artifact_entry(trip_dir, workbook, "workbook"),
        "generated_artifacts": generated_artifacts,
        "review_inputs": current_inputs,
        "accounting_profile": trip_accounting_profile(root, trip_dir),
        "trip_metadata": trip_metadata(trip_dir),
        "reconciliation_summary": reconciliation_summary,
    }
    record_path = trip_dir / f"{APPROVAL_RECORD_PREFIX}{approval_id}.json"
    write_json_atomic(record_path, manifest)

    approvals = config.get("approvals", [])
    if not isinstance(approvals, list):
        approvals = []
    approvals.append(
        {
            "approval_id": approval_id,
            "approved_at": approved_at,
            "reviewer": reviewer,
            "note": note,
            "workbook": workbook.name,
            "record_file": record_path.name,
        }
    )
    config["approvals"] = approvals
    save_trip_config(trip_dir, config)
    return approval_view(root, trip_dir, manifest)


def approval_reconciliation_check(trip_dir: Path) -> dict:
    from nlp_expenses.reconciliation import reconciliation_view

    view = reconciliation_view(trip_dir)
    if list_visible_files(trip_statements_dir(trip_dir)):
        if not view["available"] or view["stale"]:
            raise ValueError("Sync the current invoices and statements before approval.")
        needs_review = view["summary"]["needs_review_count"]
        if needs_review:
            raise ValueError(f"Resolve the {needs_review} reconciliation or policy review item(s) before approval.")
        coverage = view.get("coverage", {})
        if trip_mode(trip_dir) == "arvine" and coverage.get("gaps") and not coverage.get("confirmation"):
            raise ValueError("Acknowledge the unresolved statement coverage gaps during workbook generation.")
    return {
        "required": bool(list_visible_files(trip_statements_dir(trip_dir))),
        "synced_at": view.get("synced_at"),
        "needs_review_count": view.get("summary", {}).get("needs_review_count", 0),
        "coverage_confirmation": view.get("coverage", {}).get("confirmation"),
    }


def latest_approval_manifest(trip_dir: Path) -> dict | None:
    config = load_trip_config(trip_dir)
    approvals = config.get("approvals", [])
    if not isinstance(approvals, list) or not approvals:
        return None
    record_name = str(approvals[-1].get("record_file") or "")
    if not record_name or record_name != Path(record_name).name:
        return None
    record_path = (trip_dir / record_name).resolve()
    if record_path.parent != trip_dir.resolve() or not record_path.is_file():
        return None
    try:
        value = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def approval_is_current(root: Path, trip_dir: Path, manifest: dict | None = None) -> bool:
    manifest = manifest if manifest is not None else latest_approval_manifest(trip_dir)
    if not manifest:
        return False
    workbook_entry = manifest.get("workbook", {})
    workbook_name = str(workbook_entry.get("path") or "")
    try:
        workbook = safe_trip_child(trip_dir, workbook_name)
    except ValueError:
        return False
    if not workbook.is_file() or file_sha256(workbook) != workbook_entry.get("sha256"):
        return False
    for entry in manifest.get("generated_artifacts", []):
        if not isinstance(entry, dict):
            return False
        try:
            artifact = safe_trip_child(trip_dir, str(entry.get("path") or ""))
        except ValueError:
            return False
        if not artifact.is_file() or file_sha256(artifact) != entry.get("sha256"):
            return False
    return (
        review_input_snapshot(root, trip_dir).get("fingerprint")
        == manifest.get("review_inputs", {}).get("fingerprint")
    )


def trip_lifecycle(root: Path, trip_dir: Path) -> dict:
    config = load_trip_config(trip_dir)
    manifest = latest_approval_manifest(trip_dir)
    current_approval = approval_is_current(root, trip_dir, manifest)
    if bool(config.get("archived")):
        status = "archived"
        reason = "Trip is hidden from the active trip list."
    elif current_approval:
        status = "approved"
        reason = "Approved sources, review decisions, profile and workbook are unchanged."
    elif not list_visible_files(trip_receipts_dir(trip_dir)):
        status = "collecting"
        reason = "Add receipt or invoice scans."
    elif current_generation_record(root, trip_dir):
        status = "ready_for_review"
        reason = "A workbook exists for the current trip inputs and can be approved."
    else:
        status = "reconciling"
        reason = "Review the current inputs and generate a fresh workbook."
    return {
        "status": status,
        "label": status.replace("_", " ").title(),
        "reason": reason,
        "archived": bool(config.get("archived")),
        "approval": approval_view(root, trip_dir, manifest) if manifest else None,
        "approval_current": current_approval,
        "current_generation": current_generation_record(root, trip_dir),
    }


def current_generation_record(root: Path, trip_dir: Path) -> dict | None:
    config = load_trip_config(trip_dir)
    generated = config.get("generated_workbooks", {})
    if not isinstance(generated, dict):
        return None
    current_fingerprint = review_input_snapshot(root, trip_dir)["fingerprint"]
    candidates = []
    for workbook_name, record in generated.items():
        if not isinstance(record, dict) or record.get("input_fingerprint") != current_fingerprint:
            continue
        try:
            workbook = resolve_workbook_for_lifecycle(trip_dir, workbook_name)
        except (FileNotFoundError, ValueError):
            continue
        candidates.append(
            {
                **record,
                "workbook": workbook.name,
                "current_workbook_sha256": file_sha256(workbook),
                "manually_modified": (
                    file_sha256(workbook) != record.get("workbook_sha256_at_generation")
                    or not generated_artifacts_are_current(trip_dir, record)
                ),
            }
        )
    return max(candidates, key=lambda item: item.get("generated_at", "")) if candidates else None


def set_trip_archived(trip_dir: Path, archived: bool) -> bool:
    config = load_trip_config(trip_dir)
    config["archived"] = bool(archived)
    save_trip_config(trip_dir, config)
    return bool(archived)


def export_approved_package(root: Path, trip_dir: Path) -> Path:
    manifest = latest_approval_manifest(trip_dir)
    if not manifest:
        raise ValueError("Approve the trip before exporting its consolidation package.")
    if not approval_is_current(root, trip_dir, manifest):
        raise ValueError("The trip changed after approval. Review and approve a current workbook before exporting.")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{PACKAGE_PREFIX}{trip_dir.name}_{timestamp}"
    target = trip_dir / f"{base}.zip"
    counter = 2
    while target.exists():
        target = trip_dir / f"{base}-{counter}.zip"
        counter += 1
    temporary = trip_dir / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            paths = [
                safe_trip_child(trip_dir, entry["path"])
                for entry in manifest["review_inputs"]["artifacts"]
                if entry.get("kind")
                in {"receipt", "statement", "reconciliation", "line_item_review", "statement_settings"}
            ]
            generated_entries = manifest.get("generated_artifacts") or [manifest["workbook"]]
            paths.extend(
                safe_trip_child(trip_dir, str(entry["path"]))
                for entry in generated_entries
                if isinstance(entry, dict) and entry.get("path")
            )
            config_path = trip_dir / ".nlp-expenses.json"
            if config_path.is_file():
                paths.append(config_path)
            unique_paths = list(dict.fromkeys(path.resolve() for path in paths))
            for path in unique_paths:
                archive.write(path, path.resolve().relative_to(trip_dir.resolve()).as_posix())
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            )
            archive.writestr(
                "approval-manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def list_packages(trip_dir: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in trip_dir.iterdir()
            if path.is_file() and path.name.startswith(PACKAGE_PREFIX) and path.suffix.lower() == ".zip"
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )


def resolve_package(trip_dir: Path, filename: str) -> Path:
    target = safe_trip_child(trip_dir, filename)
    if target not in list_packages(trip_dir):
        raise FileNotFoundError("Consolidation package was not found in the selected trip.")
    return target


def approval_view(root: Path, trip_dir: Path, manifest: dict | None) -> dict | None:
    if not manifest:
        return None
    workbook = manifest.get("workbook", {})
    return {
        "approval_id": manifest.get("approval_id"),
        "approved_at": manifest.get("approved_at"),
        "reviewer": manifest.get("reviewer"),
        "note": manifest.get("note"),
        "workbook": Path(str(workbook.get("path") or "")).name,
        "artifacts": [
            {
                "kind": entry.get("kind"),
                "name": Path(str(entry.get("path") or "")).name,
            }
            for entry in manifest.get("generated_artifacts", [])
            if isinstance(entry, dict)
        ],
        "current": approval_is_current(root, trip_dir, manifest),
    }


def generation_artifacts(trip_dir: Path, workbook: Path, generation: dict) -> list[dict]:
    entries = generation.get("artifacts")
    if not isinstance(entries, list) or not entries:
        return [artifact_entry(trip_dir, workbook, "arvine_report")]
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Regenerate the report bundle before approval.")
        artifact = safe_trip_child(trip_dir, str(entry.get("path") or ""))
        if not artifact.is_file():
            raise ValueError("A generated report artifact is missing. Regenerate before approval.")
        if artifact != workbook and file_sha256(artifact) != entry.get("sha256"):
            raise ValueError("A generated report artifact changed. Regenerate before approval.")
        result.append(artifact_entry(trip_dir, artifact, str(entry.get("kind") or "generated")))
    return result


def generated_artifacts_are_current(trip_dir: Path, generation: dict) -> bool:
    entries = generation.get("artifacts")
    if not isinstance(entries, list) or not entries:
        return True
    for entry in entries:
        if not isinstance(entry, dict):
            return False
        try:
            artifact = safe_trip_child(trip_dir, str(entry.get("path") or ""))
        except ValueError:
            return False
        if not artifact.is_file() or file_sha256(artifact) != entry.get("sha256"):
            return False
    return True


def generated_artifact_kind(path: Path, primary_workbook: Path) -> str:
    if path == primary_workbook:
        return "arvine_report"
    if path.suffix.lower() == ".ndjson":
        return "reimbursement_manifest"
    if path.suffix.lower() == ".xlsx" and "ivado" in path.name.lower():
        return "ivado_report"
    return "generated"


def artifact_entry(trip_dir: Path, path: Path, kind: str) -> dict:
    return {
        "kind": kind,
        "path": path.resolve().relative_to(trip_dir.resolve()).as_posix(),
        "size": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def resolve_workbook_for_lifecycle(trip_dir: Path, filename: str) -> Path:
    target = safe_trip_child(trip_dir, filename)
    if (
        not target.is_file()
        or not target.name.startswith("expense_review_")
        or target.suffix.lower() != ".xlsx"
        or target.name.startswith("~$")
    ):
        raise FileNotFoundError("Choose a generated workbook from this trip.")
    return target


def safe_trip_child(trip_dir: Path, relative_name: str) -> Path:
    if not relative_name or Path(relative_name).is_absolute():
        raise ValueError("Invalid trip artifact path.")
    target = (trip_dir / relative_name).resolve()
    try:
        target.relative_to(trip_dir.resolve())
    except ValueError as exc:
        raise ValueError("Trip artifact path is outside the selected trip.") from exc
    return target


def list_visible_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(path for path in folder.iterdir() if path.is_file() and not path.name.startswith("."))


def write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
