# R016 — Minimal trip reimbursement contract and reports

## Implementation status

**Ready for field testing.**

The supplied IVADO workbook is the authoritative output reference. The app
produces a compact Arvine report, a three-tab IVADO workbook, and contract
`3.0.0` with producer/consumer compatibility tests.

## Decision

Keep `nlp-expenses` and `arvine-accounting-expenses` as separate projects.

- `nlp-expenses` owns receipt extraction, statement reconciliation, employee
  sharing, line-item/alcohol review, and report generation.
- `arvine-accounting-expenses` owns BMO reimbursement matching, accounting
  mappings, and reviewed Tx staging.
- The boundary is the packaged schema at
  `nlp_expenses/contracts/trip-reimbursement-manifest.v3.schema.json`.

The boundary contains only data the accounting consumer needs. It does not
carry legal identifiers, approvers, settlement legs, policy profiles, payment
references, or IVADO-template confirmations.

## Minimal user input

Trip setup requires:

1. claim program (`arvine_only` or `ivado_sponsored`);
2. traveller.

Trip dates and business purpose are optional context. The default payer is
editable once and can be overridden on each receipt.

Every receipt exposes `Employees sharing bill`, defaulting to 1. If a receipt
is shared by N employees, the report and matching amount use the traveller's
`1/N` share. The full invoice and every extracted line remain in the audit
output, so a split never destroys evidence. Statement-backed matching avoids
dividing a card transaction that already represents the traveller's share a
second time.

## Independent amounts

`paid_by` is expense-level:

- `employee_personal`;
- `arvine_corporate_bmo`.

Each receipt carries:

- `total_cad`: reviewed traveller share;
- `arvine_reimbursable_cad`: amount Arvine owes the employee;
- `ivado_claimable_cad`: amount included in the IVADO report;
- `ivado_excluded_cad`: IVADO-only removals, including alcohol.

For sponsored trips:

```text
sum(total_cad)
  = ivado_claim_total_cad + ivado_excluded_total_cad

employee_reimbursement_total_cad + corporate_paid_total_cad
  = sum(total_cad)
```

The IVADO claim is a pass-through receivable, not an Arvine travel expense.
Alcohol removed from that claim remains in the full Arvine reimbursement and
is recorded as an Arvine-borne meal expense.

All controls use a CAD 0.02 tolerance.

## Generated workbooks

### Arvine workbook

Two tabs:

1. `Expense Report`: traveller, report date, purpose, and one row per receipt
   with full receipt, `1/N` share, payer, reviewed CAD, reimbursement, and
   IVADO amounts.
2. `Accounting Rows`: posting-ready journal rows. Ordinary trips contain the
   travel/meal/tax expense booking and shareholder payment. IVADO-sponsored
   trips contain the client-recoverable travel booking, 50/50 Arvine-borne
   alcohol rows, the IVADO receivable invoice, and the bank reimbursement.

### IVADO workbook

Exactly five tabs:

1. `modèle - Template FR EN`: IVADO's official expense form, populated with
   the eligible traveller claim while retaining the supplied template,
   formulas, validation, and layout.
2. `Card Statements`: statement transactions mapped to trip receipts, including
   exact CAD, statement basis, receipt mapping, and allocated claim/removal.
3. `Reconciliation`: compact receipt-level evidence for people sharing, gross
   card share, FX, alcohol removed, IVADO claim, and traveller reimbursement.
   Line-item detail can be re-enabled with the code flag when needed.
4. `Directives & instructions - FR`: preserved official guidance.
5. `Guidelines & Instructions - EN`: preserved official guidance.

## Contract 3.0

Receipt records contain only:

- identity/date/vendor/description/category/source file;
- location, original currency/total, optional taxes, and reviewed CAD;
- payer, employee count, Arvine/IVADO inclusion and amounts;
- receipt items and their alcohol/removal decisions.

The trip report contains only:

- trip/report IDs, report date, claim program, traveller, and description;
- the four report totals;
- the five derived accounting amounts consumed downstream.

The producer is authoritative. The consumer remains compatible with contract
2.0 and legacy unversioned records, while new output uses 3.0.

Any semantic change requires a version bump, byte-identical schema copies in
both repositories, and producer/consumer fixtures.

## Acceptance criteria

- A shared invoice uses the reviewed `1/N` employee share and still shows the
  full invoice items.
- Alcohol excluded from IVADO remains visible with the amount removed.
- IVADO `Expense Report` equals `ivado_claim_total_cad`.
- Corporate-paid receipts do not increase employee reimbursement.
- In IVADO mode, Arvine reimburses the full reviewed business share. The IVADO
  claim excludes alcohol; eligible travel is posted to `Expenses Recoverable
  from Clients`, while Arvine-borne alcohol is split 50/50 between deductible
  and non-deductible meal accounts.
- Contract copies in both repositories are byte-for-byte identical.
