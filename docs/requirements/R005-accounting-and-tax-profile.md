# R005 — Accounting and Tax Profile

**Priority:** P1  
**Status:** Implemented

## Problem

The workbook currently embeds one set of accounting defaults: 50% meal
deductibility and tax recovery, 100% non-meal recovery, a shareholder current
account, and fixed account labels. These defaults are not appropriate for
every entity, registrant type, reimbursement arrangement, or business policy.

## Required outcome

Accounting assumptions must be explicit, configurable, versioned, and visible
in the workbook.

## Functional requirements

1. Store a local accounting profile outside individual trip folders, with an
   optional trip-level override.
2. Profile fields:
   - company/legal name;
   - traveller reimbursement type;
   - GST/HST registrant status;
   - QST registrant status;
   - commercial-use percentage;
   - normal and meal tax-recovery percentages;
   - normal and meal deduction percentages;
   - counter-account;
   - account mapping by expense type;
   - tax calculation method;
   - profile effective date and version.
3. New trips inherit the current default profile.
4. Generated workbooks record a snapshot of the applied profile.
5. Tax credits default to zero when the entity is not registered for the
   relevant tax.
6. Profile changes must not alter previously generated workbooks.
7. The UI must label these values as accounting assumptions and require review.

## Acceptance criteria

- A non-registrant profile produces zero GST/HST and QST recoveries.
- A corporate-card profile uses the configured credit-card liability account
  instead of shareholder reimbursement.
- A profile with a meal exception applies its configured percentages.
- Workbook journal balance checks remain valid for all profiles.

## Non-goals

- Determining legal tax eligibility automatically.
- Filing GST/HST, QST, payroll, or income-tax returns.
