# R010 — Trip Metadata and Policy Controls

**Priority:** P2  
**Status:** Implemented

## Problem

A trip is currently identified by month and description. A complete business
trip record also needs traveller, dates, destinations, business objective,
client/project, cost centre, reimbursement method, and policy context.

## Required outcome

The trip folder must hold enough structured metadata to explain why expenses
were incurred and how they should be reviewed.

## Functional requirements

1. Trip metadata fields:
   - traveller and company;
   - start and end dates;
   - origin and destinations;
   - business purpose;
   - client/project and cost centre;
   - approver;
   - reimbursement/payment method;
   - expected cards/accounts;
   - policy profile.
2. Existing trips migrate with blank optional fields.
3. Invoice business purpose inherits the trip purpose but remains editable.
4. Policy controls may define:
   - receipt-required threshold;
   - allowed categories;
   - meal limits;
   - alcohol treatment;
   - personal expense treatment;
   - mileage and per-diem rules.
5. Violations create review warnings, not silent data changes.
6. Metadata appears in the workbook summary.

## Acceptance criteria

- A new trip captures core metadata without making the creation form onerous.
- Missing required metadata blocks final approval but not initial uploads.
- Policy exceptions are visible with an explanation field.
- Existing trips continue to open.

## Non-goals

- Travel booking or itinerary management.
- Automatically determining company policy.
