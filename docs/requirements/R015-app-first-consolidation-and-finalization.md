# R015 — App-First Consolidation and Finalization

**Priority:** P0  
**Status:** Implemented

## Problem

The workbook previously served two roles: shareable output and the only place
to finish parts of the claim. This made the browser review incomplete and
allowed regenerated workbooks to lose corrections made only in Excel.

## Required outcome

All business decisions needed to produce the claim are completed in the local
app. Excel is a versioned, shareable export of that finalized state, not the
primary editing surface.

## Functional requirements

1. Use one canonical receipt review snapshot for extracted and reviewed expense
   fields, whole-expense inclusion, person count, line items, manual lines,
   alcohol decisions, and justified manual CAD.
2. Use one reconciliation contract for Arvine and IVADO statement
   transactions, automatic suggestions, manual mappings, exact CAD, and FX.
3. Show a calculated per-expense ledger before export, including original
   claim basis, person split, CAD source, FX rate, and final CAD claim.
4. Show a consolidated blocking issue queue with links to the relevant review
   areas.
5. Show the Arvine journal/accounting preview using the same profile and
   calculation inputs as the workbook.
6. Require an explicit finalization action. Store the finalized input
   fingerprint and automatically reopen the review after any source, mapping,
   metadata, profile, expense, or line change.
7. Permit Excel export only from a current finalized browser review. Preserve
   canonical CLI behavior for automation and compatibility.
8. Migrate older line-review snapshots in memory with safe defaults: included
   expense, one person, and the existing receipt total.

## Acceptance criteria

- A user can correct expense fields, exclude a whole receipt, edit/add/remove
  receipt lines, change alcohol inclusion, and set the number of people without
  opening Excel.
- A user can reconcile an IVADO foreign-currency receipt to the statement and
  see the exact CAD amount and FX in the browser.
- The app preview and generated workbook use the same person count, inclusion,
  manual CAD, line decisions, and statement mapping.
- Finalization becomes stale after any persisted review change.
- Old Melbourne review data renders as included with one person and retains its
  receipt totals.
- Service, Flask API, workbook, migration, full regression, and rendered local
  browser checks pass.

## Non-goals

- Rebuilding general spreadsheet formulas, arbitrary columns, or free-form
  workbook editing in the browser.
- Importing post-export Excel edits back into the app.
- Posting journal entries directly to accounting software.
