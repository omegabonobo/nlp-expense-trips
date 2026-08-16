# R013 — Program-Specific Line-Item Review and Alcohol Treatment

**Priority:** P1  
**Status:** Implemented

## Required outcome

Arvine and IVADO use the same extracted receipt lines but apply different,
explicit reimbursement rules:

- Arvine includes every uploaded receipt and every receipt line. Alcohol is
  neither shown as a decision nor removed from Arvine.
- IVADO uses alcohol classification to remove alcohol from the sponsor claim.
  Every line marked `is_alcohol = true` is excluded from IVADO with reason
  `alcohol`, regardless of detection confidence.
- Non-alcohol lines are included in IVADO by default. A user may remove one
  for another documented reason.

The Arvine reimbursement and IVADO sponsor claim must always be calculated
from their dedicated inclusion fields. A generic or legacy `included` alias
must never make an IVADO exclusion leak into Arvine.

## Data semantics

Each extracted or user-created line item retains:

- a stable line ID tied to the receipt and source fingerprint;
- description and amount in receipt currency;
- `included_in_arvine`, which is always `true` for uploaded receipt lines;
- `included_in_ivado`;
- `ivado_exclusion_reason`;
- `is_alcohol`, confidence, matched term, and detection reason for sponsored
  trips;
- manual-classification and IVADO-inclusion audit markers;
- an explicit marker for synthetic balancing lines.

The program decisions are coupled as follows:

1. Setting `is_alcohol = true` sets `included_in_ivado = false` and
   `ivado_exclusion_reason = alcohol`.
2. Setting `is_alcohol = false` clears an automatic alcohol exclusion and
   restores IVADO inclusion.
3. An alcoholic line cannot be included in IVADO while it remains classified
   as alcohol.
4. A non-alcohol line removed from IVADO requires one of `non_business`,
   `policy_cap`, `missing_documentation`, or `other`.
5. Arvine inclusion cannot be turned off in the receipt editor. An unrelated
   receipt should be removed from the trip, and an incorrect extracted line
   should be removed from the line review.

## User interface

### Arvine-only trip

- Show receipt descriptions, amounts, sharing count, and receipt totals.
- Do not show alcohol classification, IVADO inclusion, or IVADO exclusion
  controls.
- Explain that all uploaded receipts are included in Arvine.

### IVADO-sponsored trip

- Explain that Arvine includes the full reviewed receipt while IVADO removes
  alcohol.
- Show alcohol classification and IVADO inclusion per line.
- For alcohol, show a fixed `Alcohol — automatic` exclusion reason.
- For an included non-alcohol line, show `Included — no exclusion`.
- Enable the IVADO reason selector only for a non-alcohol line that the user
  explicitly removed from IVADO.

## Default behavior

| Condition | Arvine | IVADO |
|---|---|---|
| Line marked alcohol, any confidence | Included | Removed; reason `alcohol` |
| Explicit non-alcoholic phrase | Included | Included |
| Ordinary line item | Included | Included |
| Non-alcohol line manually removed | Included | Removed; documented reason |
| Unreadable balancing line | Included | Included; review receipt totals |

## Accounting rules

### Arvine

- The reviewed receipt total remains authoritative.
- Alcohol does not change reimbursement, GST/HST, QST, deductibility, or
  journal amounts.
- `number_of_people` applies to the full reviewed receipt.

### IVADO

- Alcohol is removed before applying `number_of_people`.
- Other line exclusions require a documented reason.
- Removed items remain visible in the IVADO `Receipt Items` sheet.
- A meal with excluded lines must reconcile to its receipt total before
  generation.

## Migration

Review snapshots before version 3 are normalized when read:

- all Arvine receipt and line inclusion values become `true`, and Arvine-only
  snapshots discard alcohol classification metadata;
- in IVADO snapshots, every existing line marked alcohol becomes excluded with
  reason `alcohol`, including low-confidence classifications;
- a stale `alcohol` exclusion on a line now classified as non-alcohol is
  cleared and the line is restored to IVADO.

Existing generated workbooks are not rewritten.

## Acceptance criteria

- An Arvine-only screen contains no alcohol or IVADO line controls.
- Attempting to exclude an uploaded receipt or line from Arvine is rejected.
- Every alcohol line is included in Arvine.
- Every alcohol line is excluded from IVADO, even below the former confidence
  threshold.
- Correcting an alcohol false positive to non-alcohol restores IVADO
  inclusion.
- A non-alcohol IVADO exclusion exposes an editable reason selector.
- The Arvine workbook contains the full reimbursable amount while the IVADO
  workbook contains the net sponsor claim and visibly lists removed alcohol.
- Existing review snapshots migrate without modifying source receipt files or
  prior generated workbooks.
- Service, UI, workbook, migration, and contract tests pass.
