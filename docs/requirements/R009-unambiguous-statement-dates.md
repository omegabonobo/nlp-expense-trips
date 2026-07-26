# R009 — Unambiguous Statement Dates

**Priority:** P1  
**Status:** Implemented

## Problem

Numeric dates such as `07/01/2026` are ambiguous. Generic parsing currently
tries day-first before month-first, which silently misinterprets valid US
exports.

## Required outcome

Ambiguous dates must never be silently interpreted without provider context or
an explicit user choice.

## Functional requirements

1. Provider-specific adapters use their documented date convention.
2. ISO dates and native spreadsheet date cells remain automatic.
3. Generic files containing ambiguous dates must:
   - infer a convention only when unambiguous rows prove it; or
   - require the user to choose day-first or month-first.
4. Store the chosen convention per statement file fingerprint.
5. Preflight must show sample parsed dates and the selected convention.
6. Reconciliation must be stale when the convention changes.

## Acceptance criteria

- `13/07/2026` proves day-first for its file.
- `07/13/2026` proves month-first for its file.
- A file containing only `07/01/2026` blocks normalization until configured.
- ISO `2026-07-01` requires no prompt.

## Non-goals

- Locale inference from the Mac operating-system setting alone.
