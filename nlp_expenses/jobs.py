from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

from nlp_expenses.generator import generate_review
from nlp_expenses.line_items import sync_line_item_review
from nlp_expenses.models import GenerationProgress
from nlp_expenses.reconciliation import sync_reconciliation
from nlp_expenses.trips import trip_mode, trip_statements_dir
from nlp_expenses.ui_services import list_source_files, resolve_trip, versioned_output_path


ACTIVE_STATUSES = {"queued", "running"}


class JobConflictError(RuntimeError):
    pass


@dataclass
class GenerationJob:
    id: str
    trip_name: str
    quality: str
    kind: str = "generation"
    status: str = "queued"
    stage: str = "queued"
    current: int = 0
    total: int = 1
    message: str = "Waiting to start"
    warnings: list[str] = field(default_factory=list)
    output_name: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class JobManager:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._jobs: dict[str, GenerationJob] = {}
        self._latest_by_trip: dict[str, str] = {}
        self._latest_reconciliation_by_trip: dict[str, str] = {}
        self._latest_line_items_by_trip: dict[str, str] = {}
        self._mutating_trips: set[str] = set()
        self._lock = threading.Lock()

    def start(self, trip_name: str, quality: str, statements_complete: bool) -> GenerationJob:
        if quality not in {"basic", "best"}:
            raise ValueError("Choose Basic or Best extraction quality.")
        trip = resolve_trip(self.root, trip_name)
        with self._lock:
            if trip_name in self._mutating_trips:
                raise JobConflictError("Source files are being updated for this trip. Try generation again shortly.")
            existing = self._active_job(trip_name)
            if existing and existing.status in ACTIVE_STATUSES:
                raise JobConflictError("Another sync or workbook job is already running for this trip.")
            job = GenerationJob(id=uuid.uuid4().hex, trip_name=trip_name, quality=quality)
            if trip_mode(trip) == "arvine" and not list_source_files(trip_statements_dir(trip)):
                job.warnings.append("No statement files were included; statement matching will be empty.")
            self._jobs[job.id] = job
            self._latest_by_trip[trip_name] = job.id

        worker = threading.Thread(
            target=self._run,
            args=(job.id, trip, statements_complete),
            daemon=True,
            name=f"nlp-expenses-{trip_name}",
        )
        worker.start()
        return self.get(job.id)

    def start_reconciliation(self, trip_name: str, quality: str) -> GenerationJob:
        if quality not in {"basic", "best"}:
            raise ValueError("Choose Basic or Best extraction quality.")
        trip = resolve_trip(self.root, trip_name)
        with self._lock:
            if trip_name in self._mutating_trips:
                raise JobConflictError("Source files are being updated for this trip. Try sync again shortly.")
            existing = self._active_job(trip_name)
            if existing and existing.status in ACTIVE_STATUSES:
                raise JobConflictError("Another sync or workbook job is already running for this trip.")
            job = GenerationJob(
                id=uuid.uuid4().hex,
                trip_name=trip_name,
                quality=quality,
                kind="reconciliation",
                message="Waiting to sync invoices and statements",
            )
            self._jobs[job.id] = job
            self._latest_reconciliation_by_trip[trip_name] = job.id

        worker = threading.Thread(
            target=self._run_reconciliation,
            args=(job.id, trip),
            daemon=True,
            name=f"nlp-expenses-reconcile-{trip_name}",
        )
        worker.start()
        return self.get(job.id)

    def start_line_item_review(self, trip_name: str, quality: str) -> GenerationJob:
        if quality not in {"basic", "best"}:
            raise ValueError("Choose Basic or Best extraction quality.")
        trip = resolve_trip(self.root, trip_name)
        with self._lock:
            if trip_name in self._mutating_trips:
                raise JobConflictError("Source files are being updated for this trip. Try scanning again shortly.")
            existing = self._active_job(trip_name)
            if existing and existing.status in ACTIVE_STATUSES:
                raise JobConflictError("Another sync or workbook job is already running for this trip.")
            job = GenerationJob(
                id=uuid.uuid4().hex,
                trip_name=trip_name,
                quality=quality,
                kind="line_items",
                message="Waiting to scan receipt line items",
            )
            self._jobs[job.id] = job
            self._latest_line_items_by_trip[trip_name] = job.id

        worker = threading.Thread(
            target=self._run_line_item_review,
            args=(job.id, trip),
            daemon=True,
            name=f"nlp-expenses-line-items-{trip_name}",
        )
        worker.start()
        return self.get(job.id)

    def get(self, job_id: str) -> GenerationJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise FileNotFoundError("Generation job was not found.")
            return GenerationJob(**job.to_dict())

    def latest(self, trip_name: str) -> GenerationJob | None:
        with self._lock:
            job_id = self._latest_by_trip.get(trip_name)
            job = self._jobs.get(job_id or "")
            return GenerationJob(**job.to_dict()) if job else None

    def latest_reconciliation(self, trip_name: str) -> GenerationJob | None:
        with self._lock:
            job_id = self._latest_reconciliation_by_trip.get(trip_name)
            job = self._jobs.get(job_id or "")
            return GenerationJob(**job.to_dict()) if job else None

    def latest_line_item_review(self, trip_name: str) -> GenerationJob | None:
        with self._lock:
            job_id = self._latest_line_items_by_trip.get(trip_name)
            job = self._jobs.get(job_id or "")
            return GenerationJob(**job.to_dict()) if job else None

    def active_for_trip(self, trip_name: str) -> GenerationJob | None:
        with self._lock:
            job = self._active_job(trip_name)
            return GenerationJob(**job.to_dict()) if job else None

    @contextmanager
    def mutation_guard(self, trip_name: str) -> Iterator[None]:
        """Prevent source mutations from overlapping a trip job."""

        with self._lock:
            active = self._active_job(trip_name)
            if active:
                raise JobConflictError(
                    "This trip is being processed. Wait for the current sync or workbook job to finish."
                )
            if trip_name in self._mutating_trips:
                raise JobConflictError("Another source-file update is already running for this trip.")
            self._mutating_trips.add(trip_name)
        try:
            yield
        finally:
            with self._lock:
                self._mutating_trips.discard(trip_name)

    def _run(self, job_id: str, trip: Path, statements_complete: bool) -> None:
        mode = trip_mode(trip)
        # The primary artifact is always the Arvine reimbursement/accounting
        # report. Sponsored trips add a sibling IVADO claim adapter.
        output_path = versioned_output_path(trip, "arvine")
        self._update(
            job_id,
            status="running",
            stage="starting",
            message="Starting generation",
            started_at=datetime.now().isoformat(timespec="seconds"),
        )
        try:
            result = generate_review(
                trip,
                self.root,
                llm_mode="required" if self.get(job_id).quality == "best" else "off",
                mode=mode,
                statements_complete=statements_complete,
                output_path=output_path,
                progress_callback=lambda event: self._progress(job_id, event),
                warning_callback=lambda warning: self._warning(job_id, warning),
                allow_openai_prompt=False,
                contract_bundle=True,
            )
            if result is None:
                raise RuntimeError("Generation was cancelled before the workbook was created.")
            warnings = self.get(job_id).warnings
            self._update(
                job_id,
                status="succeeded_warnings" if warnings else "succeeded",
                stage="complete",
                current=1,
                total=1,
                message="Workbook ready with warnings" if warnings else "Workbook ready",
                output_name=result.name,
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )
        except Exception as exc:
            output_path.unlink(missing_ok=True)
            self._update(
                job_id,
                status="failed",
                stage="failed",
                message="Generation failed",
                error=str(exc) or exc.__class__.__name__,
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )

    def _run_reconciliation(self, job_id: str, trip: Path) -> None:
        self._update(
            job_id,
            status="running",
            stage="starting",
            message="Starting invoice and statement sync",
            started_at=datetime.now().isoformat(timespec="seconds"),
        )
        try:
            sync_reconciliation(
                trip,
                self.root,
                llm_mode="required" if self.get(job_id).quality == "best" else "off",
                progress_callback=lambda event: self._progress(job_id, event),
                warning_callback=lambda warning: self._warning(job_id, warning),
                allow_openai_prompt=False,
            )
            warnings = self.get(job_id).warnings
            self._update(
                job_id,
                status="succeeded_warnings" if warnings else "succeeded",
                stage="complete",
                current=1,
                total=1,
                message="Reconciliation ready with warnings" if warnings else "Reconciliation ready",
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )
        except Exception as exc:
            self._update(
                job_id,
                status="failed",
                stage="failed",
                message="Reconciliation sync failed",
                error=str(exc) or exc.__class__.__name__,
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )

    def _run_line_item_review(self, job_id: str, trip: Path) -> None:
        self._update(
            job_id,
            status="running",
            stage="starting",
            message="Starting receipt line-item scan",
            started_at=datetime.now().isoformat(timespec="seconds"),
        )
        try:
            sync_line_item_review(
                trip,
                self.root,
                llm_mode="required" if self.get(job_id).quality == "best" else "off",
                progress_callback=lambda event: self._progress(job_id, event),
                warning_callback=lambda warning: self._warning(job_id, warning),
                allow_openai_prompt=False,
            )
            warnings = self.get(job_id).warnings
            self._update(
                job_id,
                status="succeeded_warnings" if warnings else "succeeded",
                stage="complete",
                current=1,
                total=1,
                message="Line items ready with warnings" if warnings else "Line items ready for review",
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )
        except Exception as exc:
            self._update(
                job_id,
                status="failed",
                stage="failed",
                message="Receipt line-item scan failed",
                error=str(exc) or exc.__class__.__name__,
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )

    def _progress(self, job_id: str, event: GenerationProgress) -> None:
        self._update(
            job_id,
            stage=event.stage,
            current=event.current,
            total=max(event.total, 1),
            message=event.message,
        )

    def _warning(self, job_id: str, warning: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if warning not in job.warnings:
                job.warnings.append(warning)

    def _update(self, job_id: str, **changes) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)

    def _active_job(self, trip_name: str) -> GenerationJob | None:
        return next(
            (
                job
                for job in self._jobs.values()
                if job.trip_name == trip_name and job.status in ACTIVE_STATUSES
            ),
            None,
        )
