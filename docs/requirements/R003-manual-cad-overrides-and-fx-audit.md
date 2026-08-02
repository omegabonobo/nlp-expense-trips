# R003 — Manual CAD Overrides and FX Audit

**Priority:** P0  
**Status:** Implemented

## Problem

Some statement providers, especially multi-currency providers, do not expose a
complete CAD settlement. Those transactions need a consistent weekly CAD
conversion with a visible source, while manual CAD remains the fallback when no
supported market rate is available.

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
4. When no exact CAD amount exists, the provider-neutral normalization layer
   converts the transaction's purchase amount and currency using the arithmetic
   average of the published business-day CAD rates in the transaction's
   Monday–Sunday week.
5. Direct Bank of Canada currency/CAD observations are preferred. QAR uses the
   official 3.64 QAR/USD peg and the Bank of Canada weekly USD/CAD average when
   no direct official QAR/CAD series is available.
6. Weekly rates are cached per trip with their week, observations, method,
   conversion route, source URLs, and retrieval timestamp so regeneration is
   deterministic and auditable.
7. The UI must clearly distinguish:
   - exact statement CAD;
   - aggregated exact statement CAD;
   - weekly externally converted CAD;
   - manual CAD;
   - unavailable CAD.
8. FX rate equals CAD amount used divided by the corrected original total.
9. Manual CAD must take precedence over statement or externally converted CAD only after explicit user
   action.
10. Removing the override restores the normalized amount.
11. Workbook fields and review checks must reflect the source of the amount.

## Acceptance criteria

- An incomplete Wise transaction can be mapped, assigned a manual CAD value,
  and leave no CAD-completeness warning.
- A Wise QAR purchase funded from a non-CAD balance uses the QAR target amount
  and the cached weekly QAR/CAD conversion, never the Wise export's exchange-rate
  field.
- The same weekly conversion enriches a provider-neutral future-card import
  whose settlement is not already in CAD.
- A Wise row with `Status=REFUNDED` is negative even when its exported
  `Direction` is `OUT`.
- The override and note persist through resync and generation.
- A zero or negative override is rejected for a normal purchase.
- Refund/credit behavior follows R004.

## Non-goals

- Treating a weekly market conversion as an exact card-provider CAD settlement.
- Using a provider-exported exchange-rate field when neither the purchase nor
  settlement currency is CAD.
