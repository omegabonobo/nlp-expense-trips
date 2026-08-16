# R007 — Normalization Warnings and Duplicate Resolution

**Priority:** P0  
**Status:** Implemented

## Problem

Row-level normalization issues and possible duplicates are available in the
normalized transaction model and final workbook but are not fully represented
in the frontend reconciliation view. This can cause double counting or hide a
statement row that needs interpretation.

## Required outcome

Every warning that can affect amount, date, eligibility, or duplication must be
visible and resolvable before generation.

## Functional requirements

1. Reconciliation groups must expose:
   - normalization status;
   - review notes;
   - possible-duplicate status;
   - source files and source rows;
   - funding-leg detail.
2. The UI must visually distinguish blocking errors, review warnings, and
   informational audit rows.
3. Possible duplicates must offer:
   - keep both;
   - ignore selected transaction;
   - confirm legitimate repeated transactions.
4. Unresolved possible duplicates must count as `needs review`.
5. Ignored transactions remain in the workbook with reason and timestamp.
6. The sync job warning list must include cross-file duplicate findings.

## Acceptance criteria

- Two identical non-Wise rows appear as unresolved possible duplicates.
- The user can ignore one and the invoice CAD total uses only the retained row.
- Confirming both prevents future duplicate warnings for that snapshot.
- Source file and row remain visible for audit.

## Non-goals

- Automatically deleting ambiguous non-Wise duplicates.
