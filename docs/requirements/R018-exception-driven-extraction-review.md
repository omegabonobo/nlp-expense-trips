# R018 — Exception-Driven Extraction Review

**Priority:** P1
**Status:** Implemented

## Problem

Receipt extraction still requires broad manual review even when most fields are
reliable. Best-quality results can also vary between runs, and a rescan does not
make every meaningful change easy to identify.

## Required outcome

The app directs the user to uncertain or changed extraction results while
keeping the full receipt review available for audit.

## Functional requirements

1. Store confidence and a short reason for each extracted receipt field and
   line item.
2. Classify each result as ready, needs review, or blocking using deterministic
   validation rules in addition to extractor confidence.
3. Present one exception queue covering missing fields, low-confidence values,
   line-total differences, uncertain alcohol classification, and likely
   duplicates.
4. Allow the user to open the source receipt beside the exact field or line
   requiring review.
5. Preserve explicit user corrections and review decisions across rescans when
   the source file has not changed.
6. Show a before/after diff when a rescan changes a previously extracted or
   reviewed value; never silently replace an explicit user correction.
7. Keep a full-review view so every extracted value remains inspectable.
8. Maintain a versioned, privacy-safe benchmark set and report field coverage,
   field accuracy, line-total agreement, blocking count, review count, runtime,
   and extractor version.

## Acceptance criteria

- A receipt with complete, internally consistent, high-confidence extraction
  can be marked ready without opening every field.
- Every blocking or low-confidence result appears once in the exception queue
  and links to the relevant review control.
- Rescanning an unchanged receipt preserves manual corrections and displays
  material automatic changes.
- Benchmark results can compare Basic and Best quality between releases.
- Finalization still requires all blocking exceptions to be resolved.

## Implementation notes

- Review snapshots now store versioned confidence evidence for every receipt
  field and line, plus deterministic ready, review, and blocking classifications.
- One exception queue covers required fields, low-confidence values and lines,
  total differences, uncertain alcohol, likely duplicates, and rescan changes.
- Exception actions open the exact field or line, with the source receipt
  available in a separate browser view; the complete receipt review remains
  visible below the queue.
- Unchanged-source rescans retain corrections and review decisions. Material
  automatic changes are recorded as before/after values and require explicit
  acknowledgment without deleting the audit history.
- `make benchmark-extraction` runs Basic and Best against the versioned,
  synthetic benchmark set and reports coverage, accuracy, line-total
  agreement, exception counts, runtime, and extractor version.

## Non-goals

- Removing human review or guaranteeing perfect extraction.
- Expanding hotel, airfare, mileage, or per-diem accounting behavior.
- Changing the existing meal accounting or IVADO pass-through treatment.
