# R001 — Reconciliation Freshness and Job Locking

**Priority:** P0  
**Status:** Implemented

## Problem

The application detects when receipts or statements changed after the most
recent reconciliation, but it currently treats that state as a warning only.
Workbook generation can reuse stale manual mappings. Uploads, removals, and
mode changes can also occur while a reconciliation or generation job is
running, creating an inconsistent input snapshot.

The automatic-match confidence stored beside a preserved manual override can
also be replaced by the manual confidence during a resync.

## Required outcome

Every generated workbook must be based on one stable, identifiable set of
source files. Manual mappings may be applied only when they belong to the
current source fingerprint.

## User stories

- As a user, I am prevented from accidentally generating with stale mappings.
- As a user, I can intentionally generate without reconciliation when there
  are no statements, or after explicitly accepting unresolved items.
- As a user, I cannot add, remove, or reinterpret source files while a job is
  using them.
- As a user, restoring an automatic mapping shows its original confidence.

## Functional requirements

1. An Arvine trip with statement files must have a non-stale reconciliation
   before generation.
2. A trip with zero statement files may generate after the existing
   statements-complete confirmation.
3. Generation must validate freshness again inside the background worker,
   immediately before extraction begins.
4. Manual mappings must be ignored or rejected when their reconciliation
   fingerprint differs from the current source fingerprint.
5. Upload, remove-file, and mode-change requests must return HTTP `409` while
   any job for the trip is queued or running.
6. The UI must disable the same controls while a job is active and explain why.
7. The job manager must expose a read-only trip activity query.
8. A resync must preserve the original automatic match confidence separately
   from manual confidence.
9. Failed or interrupted generation must not leave a partial workbook.

## API behavior

- Generation with stale or missing required reconciliation: HTTP `400` with an
  actionable message.
- Source mutation during an active job: HTTP `409`.
- Job payloads continue to use the current polling schema.

## Acceptance criteria

- Changing a receipt after sync makes generation fail until resync.
- Changing a statement after sync makes generation fail until resync.
- Adding the first statement after a zero-statement run requires sync.
- A background job blocks uploads, removal, and mode switching for that trip.
- Other trips remain editable and may run their own jobs.
- Restoring automatic after a manual override and resync restores the original
  confidence, not `100%`.
- Existing no-statement Arvine and IVADO workflows remain compatible.

## Non-goals

- Persisting running jobs across application restarts.
- Operating-system-level locks against direct Finder edits.
