# R012 — IVADO Frontend Parity

**Priority:** P2  
**Status:** Implemented

## Delivery decision

IVADO now uses the same editable receipt model, statement-mapping contract,
issue queue, calculated claim preview, and finalization gate as Arvine. Its
workbook shape remains intentionally distinct. The permissive IVADO statement
adapter continues to support CSV, XLS, XLSX, and PDF.

## Problem

IVADO remains selectable and generates a valid workbook, but statement
preflight, reconciliation, and invoice corrections are largely deferred to the
workbook. This creates inconsistent behavior between modes.

## Required outcome

IVADO must use the same trip-management, input-safety, review, and progress
conventions as Arvine while retaining IVADO-specific line items, alcohol
handling, and per-person calculations.

## Functional requirements

1. Add IVADO statement preflight for CSV, XLS, XLSX, and PDF.
2. Add frontend transaction-to-expense reconciliation.
3. Complete the shared line-item and alcohol review defined in R013.
4. Expose `number_of_person` before generation.
5. Preserve IVADO formulas and existing canonical CLI behavior.
6. Apply R001, R007, R008, and R009 controls to IVADO.
7. Clearly label mode-specific accounting behavior.

## Acceptance criteria

- The Melbourne smoke trip can be reviewed and reconciled from the frontend.
- Alcohol and per-person corrections persist into the generated workbook.
- Existing IVADO trips and workbooks remain untouched.
- Arvine and IVADO navigation use consistent interaction patterns.

## Non-goals

- Making IVADO and Arvine workbooks structurally identical.
- Applying Arvine's corporation-specific tax journal to IVADO exports.
