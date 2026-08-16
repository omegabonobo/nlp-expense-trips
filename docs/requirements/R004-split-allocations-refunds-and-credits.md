# R004 — Split Allocations, Refunds, and Credits

**Priority:** P1  
**Status:** Implemented

## Problem

The current model maps one statement transaction group to one invoice. It
cannot allocate one consolidated charge across several invoices, split
business and personal portions, or explicitly connect a later refund to the
original purchase.

## Required outcome

Reconciliation must support auditable many-to-many allocations while retaining
simple one-to-one mapping as the default interaction.

## Functional requirements

1. A transaction group may contain one or more allocation rows.
2. An allocation includes:
   - invoice filename or non-reimbursable category;
   - original-currency amount when known;
   - CAD amount;
   - allocation percentage;
   - note;
   - allocation type: purchase, refund, fee, personal, ignored.
3. Allocation totals must reconcile to the complete statement CAD amount within
   one cent.
4. A refund may reduce one or more invoice CAD totals.
5. Personal and ignored allocations must remain visible in the statement audit
   but must not enter the reimbursement journal.
6. The simple mapping selector remains available when a transaction is fully
   assigned to one invoice.
7. The UI must display unallocated and overallocated balances.
8. Workbook statement and detail sheets must preserve allocation provenance.

## Acceptance criteria

- One CAD 300 charge can be allocated CAD 100 and CAD 200 to two invoices.
- A CAD 20 refund can reduce the correct invoice without distorting unrelated
  FX rates.
- A personal portion is excluded from reimbursement but remains auditable.
- Generation is blocked for an overallocated transaction.

## Non-goals

- General-ledger line editing in the browser.
- Automatic inference of personal use.
