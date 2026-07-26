# R006 — Statement Coverage and Completeness

**Priority:** P1  
**Status:** Implemented

## Problem

The statements-complete checkbox is an unsupported assertion. The application
does not summarize which accounts, providers, or date ranges were supplied, or
identify overlapping exports and likely gaps around the trip period.

## Required outcome

Before confirmation, the user must see a coverage summary and explicitly
acknowledge any detected gaps.

## Functional requirements

1. Derive per provider/account:
   - source files;
   - earliest and latest transaction dates;
   - normalized transaction count;
   - match-eligible transaction count;
   - duplicate and overlap indicators.
2. Compare statement coverage with trip start/end dates from R010.
3. Detect likely gaps when:
   - no transaction range overlaps the trip;
   - expected accounts have no file;
   - files overlap with strong duplicate activity;
   - statement dates do not cover a configurable buffer around the trip.
4. Allow users to register expected cards/accounts for the trip.
5. Require an explicit reason when confirming with unresolved coverage gaps.
6. Record the coverage summary and confirmation in reconciliation metadata.

## Acceptance criteria

- The dashboard lists each detected provider/account and date range.
- A missing expected account blocks ordinary confirmation.
- The user can acknowledge a legitimate gap with a note.
- The workbook summary records the confirmation and unresolved warnings.

## Non-goals

- Connecting directly to bank APIs.
- Proving that a bank export itself is complete.
