# R002 — Invoice Review and Correction

**Priority:** P0  
**Status:** Implemented

## Problem

Receipt extraction feeds matching and FX calculations, but the frontend does
not let the user correct extracted fields before reconciliation. An incorrect
date, amount, currency, or vendor therefore produces poor mappings and an
incorrect accounting rate until the user edits the final workbook.

## Required outcome

The trip dashboard must provide a lightweight invoice-review step whose saved
corrections become the source of truth for reconciliation and workbook
generation.

## User stories

- As a user, I can correct an OCR total or currency before matching.
- As a user, I can record business purpose and meal attendees once, before
  generating Excel.
- As a user, I can distinguish extracted values from my overrides.
- As a user, I can restore an invoice to its latest extracted value.

## Functional requirements

1. Reconciliation must persist an invoice snapshot keyed by receipt filename.
2. Editable fields:
   - invoice date;
   - vendor;
   - description;
   - expense type;
   - original total;
   - currency;
   - country and province;
   - GST/HST and QST amounts;
   - GST/HST and QST registration numbers;
   - business purpose;
   - meal attendees/client.
3. Corrections must be stored separately from extracted data.
4. Corrected values must be applied before matching, FX calculation, tax
   checks, and workbook creation.
5. Input validation must reject invalid dates, currencies, negative purchase
   totals, and tax amounts greater than the invoice total.
6. The UI must show extraction status, review notes, and which fields are
   overridden.
7. New extraction runs must preserve valid user corrections.
8. Removing or renaming a receipt must mark its correction record obsolete
   without applying it to another file.

## Data and API

- Extend reconciliation metadata with `invoice_overrides`.
- Add a mutation endpoint for one invoice and a restore-extracted action.
- Never write corrections into the original receipt file.

## Acceptance criteria

- Correcting amount or currency changes automatic match scoring and displayed
  FX rate after resync.
- Corrected values appear in the next generated workbook.
- Invalid values produce field-specific errors.
- Overrides survive application restart and a later sync.

## Non-goals

- Editing the receipt image or PDF.
- Multi-line invoice itemization in Arvine mode.
