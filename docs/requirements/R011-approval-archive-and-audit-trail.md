# R011 — Approval, Archive, and Audit Trail

**Priority:** P2  
**Status:** Implemented

## Problem

Versioned workbooks preserve outputs, but the application has no formal
review-complete state, approval record, immutable snapshot, trip archive, or
portable backup.

## Required outcome

Users must be able to distinguish work in progress from an approved,
reproducible consolidation package.

## Functional requirements

1. Trip lifecycle states:
   - collecting;
   - reconciling;
   - ready for review;
   - approved;
   - archived.
2. Approval requires:
   - fresh reconciliation;
   - resolved blocking warnings;
   - generated workbook;
   - reviewer name;
   - approval timestamp and note.
3. Approval stores file hashes for receipts, statements, reconciliation
   metadata, applied profile, and workbook.
4. Later changes reopen the trip and preserve the prior approval record.
5. Export a ZIP package containing source files, metadata, reconciliation state,
   workbook, and manifest.
6. Archive hides a trip from the default list without deleting it.
7. Provide a restore-from-archive action.

## Acceptance criteria

- An approved trip can be reproduced and its manifest hashes verified.
- Editing a source file changes the lifecycle back to review required.
- Archive and restore do not move files outside the repository `trips/` root.

## Non-goals

- Legally binding electronic signatures.
- Cloud storage or multi-user permissions.
