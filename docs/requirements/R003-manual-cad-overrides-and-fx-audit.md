# R003 — Manual CAD Overrides and FX Audit

**Priority:** P0  
**Status:** Implemented

## Problem

Some statement providers, especially multi-currency providers, do not expose a
complete CAD settlement. The workbook supports a manual CAD override, but the
frontend cannot finish the reconciliation or explain the source of that
override.

## Required outcome

The user must be able to provide a controlled CAD amount for an invoice or
allocation when the statement does not contain a complete CAD settlement. The
tool must retain an audit explanation and calculate the accounting FX rate.

## Functional requirements

1. Each invoice may have a manual CAD override.
2. An override requires:
   - CAD amount;
   - reason/source note;
   - update timestamp.
3. Optional supporting reference:
   - statement transaction;
   - provider conversion confirmation;
   - other documented rate source.
4. The UI must clearly distinguish:
   - exact statement CAD;
   - aggregated exact statement CAD;
   - manual CAD;
   - unavailable CAD.
5. FX rate equals CAD amount used divided by the corrected original total.
6. Manual CAD must take precedence over statement CAD only after explicit user
   action.
7. Removing the override restores the statement-derived amount.
8. Workbook fields and review checks must reflect the source of the amount.

## Acceptance criteria

- An incomplete Wise transaction can be mapped, assigned a manual CAD value,
  and leave no CAD-completeness warning.
- The override and note persist through resync and generation.
- A zero or negative override is rejected for a normal purchase.
- Refund/credit behavior follows R004.

## Non-goals

- Downloading market FX rates automatically.
- Treating an estimated market rate as a card-provider settlement.
