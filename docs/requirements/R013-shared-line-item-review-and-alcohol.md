# R013 — Shared Line-Item Review and Alcohol Treatment

**Priority:** P1  
**Status:** In progress

## Problem

IVADO originally extracted restaurant line items and labelled likely alcohol,
but its review was Excel-first. The workbook subtracted rows whose
`is_alcohol` value was `TRUE`, and a user could include one again only by
changing that classification to `FALSE`. This conflated two different facts:

1. what the item appears to be; and
2. whether the user has chosen to include it in the claim.

Arvine originally removed extracted line items before reconciliation and
workbook creation, so it could not offer the same review.

## Required outcome

Every mode provides a browser line-item review with an explicit accounting
inclusion choice. IVADO automatically deactivates confidently detected alcohol.
The user may reactivate it without erasing the alcohol classification or the
reason for that classification.

## Data semantics

Each extracted or user-created line item must retain:

- a stable line ID tied to the receipt and source fingerprint;
- description and amount in receipt currency;
- extraction confidence and source (`local`, `OpenAI`, or `manual`);
- `is_alcohol`, alcohol confidence, matched term, and detection reason;
- `included`, the user's accounting/reimbursement choice;
- whether `included` or `is_alcohol` was manually overridden;
- the time and reason for a manual override;
- an explicit marker for synthetic balancing lines.

`is_alcohol` and `included` are independent:

- changing `included` never changes `is_alcohol`;
- changing `is_alcohol` reruns the mode default only when the user explicitly
  asks to reset inclusion;
- manual choices win over later automatic classification while the source
  fingerprint is unchanged;
- source-file changes make prior review stale and require confirmation.

## Default behavior

| Condition | IVADO | Arvine |
|---|---|---|
| Confident alcohol detection | `included = false` | `included = true`, visibly flagged |
| Possible/low-confidence alcohol | included, requires review | included, requires review |
| Explicit non-alcoholic phrase | included | included |
| Ordinary line item | included | included |
| Unreadable balancing line | included, requires review | included, requires review |

The Arvine default follows the current direction that only IVADO
automatically deactivates alcohol. It must remain configurable if Arvine's
accounting policy later requires automatic exclusion.

## Functional requirements

1. Extract purchased line items for meal receipts in both modes.
2. Show every extracted item, including included, excluded, low-confidence,
   and synthetic balancing rows.
3. Provide an `Include` switch per row and clear visual treatment for
   deactivated rows.
4. Show alcohol status, confidence, matched term, and plain-language reason.
5. Allow a user to:
   - include or exclude any line, regardless of mode;
   - correct the alcohol classification;
   - edit description and amount;
   - add or remove a manual line;
   - reset one receipt to automatic results.
6. Provide receipt-level actions:
   - include all;
   - apply the mode default;
   - exclude all confidently detected alcohol.
7. Show line total, included total, excluded total, receipt total, and any
   unreconciled difference before generation.
8. Persist review choices locally and apply them to versioned workbooks.
9. Write both classification evidence and inclusion decisions into the
   workbook so a reviewer can audit the result.
10. Never silently remove alcohol or another line from the visible list.
11. Continue workbook generation with warnings when reliable line-level
    extraction is unavailable; do not invent menu items.

## Detection requirements

The local detector must:

- normalize case, punctuation, accents, and known OCR variants;
- recognize general alcohol categories, beer styles, wine grapes/styles,
  spirits, cocktails, and distinctive brands;
- recognize explicit non-zero ABV values;
- give explicit non-alcoholic terms precedence, including `0.0%`,
  `non-alcoholic`, `alcohol-free`, `mocktail`, and `virgin`;
- protect common false positives including ginger/root beer, beer batter,
  cooking wine, wine vinegar, and Americano coffee;
- return a decision, confidence, reason, and matched evidence;
- use a precision-first threshold for IVADO automatic deactivation;
- allow OpenAI classification to supplement local detection without
  overriding explicit non-alcoholic evidence.

Vocabulary should be maintained in reviewed categories and informed by
authoritative cocktail and beverage-style references rather than a single
unstructured keyword list.

## Accounting rules

### IVADO

- The claimable amount is based on included line amounts.
- `number_of_person` applies after excluded line items are removed.
- A receipt whose line items do not reconcile remains visibly in review.
- Existing generated workbooks remain valid and are never rewritten.

### Arvine

Arvine must not change GST/HST, QST, deductible, or journal amounts merely
because a UI line is toggled until the allocation rule below is confirmed.
The implementation must then use one documented rule consistently in browser,
reconciliation snapshot, and workbook.

Proposed rule: when line items reconcile to the receipt total, allocate
subtotal and taxes proportionally to the included gross amount. When they do
not reconcile, require either a manual included-CAD amount and note or an
explicit user acknowledgement before generation.

## Confirmed decisions

Confirmed on 2026-07-25:

1. Arvine flags detected alcohol but leaves it included by default.
2. Arvine partial claims allocate claimable CAD, GST/HST, and QST
   proportionally to the included gross meal lines.
3. The first complete extraction/editor scope is restaurant and meal receipts
   in both modes. Hotel folios and other invoice types remain a later extension.
4. Inclusion and classification changes are audit-marked. A note is supported
   by the service model but is not required for a simple reactivation.

## Acceptance criteria

- An IVADO meal shows every extracted line in the browser.
- A high-confidence alcoholic item starts deactivated and retains its alcohol
  badge and detection reason when the user reactivates it.
- A false-positive protection such as `Virgin Mojito` or `Heineken 0.0%`
  starts included and non-alcoholic.
- The same inclusion switch is available in Arvine.
- Review choices survive restart and appear in a newly generated workbook.
- Source changes make stored choices visibly stale.
- Included/excluded totals and the generated result agree exactly.
- Existing trips and prior workbook bytes are untouched.
- Service, UI, workbook, stale-state, and fallback tests pass.

## Delivery slices

1. **Implemented:** expanded explainable local detector and workbook evidence.
2. **Implemented:** persistent shared line-item review model, receipt-only
   freshness fingerprint, mutation API, and background scan job.
3. **Implemented:** shared browser visibility, inclusion and alcohol controls,
   reset-to-automatic action, totals, warnings, and generation gates.
4. **Implemented:** IVADO automatic alcohol deactivation and
   inclusion-aware/person-aware workbook formula.
5. **Implemented:** Arvine included-by-default flags, proportional claim/tax
   formulas, and an editable `expense_line_items` workbook sheet.
6. **Implemented:** service, UI, workbook, stale-state, job, and rendered
   browser smoke tests.
7. **Next:** browser editing of descriptions/amounts, manual row creation, and
   later line extraction for hotel folios and other non-meal invoices.
