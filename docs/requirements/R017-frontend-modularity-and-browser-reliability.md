# R017 — Frontend Modularity and Browser Reliability

**Priority:** P0
**Status:** Implemented

## Problem

The browser interface has grown into one large template and one large JavaScript
file. Features share DOM queries, reload behavior, job polling, API calls, and
error handling, so a change in one workflow can unintentionally affect another.

## Required outcome

The frontend is organized by product workflow, has shared infrastructure for
common behavior, and can be changed safely without altering the current user
workflow.

## Functional requirements

1. Split frontend code into focused modules for trip setup, source files,
   receipt review, reconciliation, finalization, and outputs.
2. Split the server-rendered page into named template sections or components
   with explicit inputs.
3. Use one shared API client for CSRF, JSON parsing, errors, and request state.
4. Use one shared implementation for notifications, dialogs, job polling, and
   busy/disabled controls.
5. Update the affected section after ordinary edits instead of reloading the
   full page wherever practical.
6. Preserve deep links, form behavior, keyboard use, and accessible labels.
7. Add browser tests for each main workflow and for API failure, stale state,
   and background-job completion.
8. Keep the existing Flask API and persisted trip formats compatible unless a
   separate requirement explicitly changes them.

## Acceptance criteria

- No single frontend module owns more than one main product workflow.
- Receipt editing, card matching, finalization, generation, and archive/restore
  pass automated browser tests.
- Failed saves remain visible, retain the user's input, and can be retried.
- A completed background job updates the relevant UI without losing unsaved
  work elsewhere on the page.
- Existing Python, Flask, workbook, and characterization tests continue to
  pass.

## Implementation notes

- The browser code is split into shared shell/job infrastructure and focused
  trip setup, source file, receipt, reconciliation, finalization, and output
  modules.
- The dashboard is composed from named Jinja partials while preserving the
  existing routes, element labels, and persisted trip formats.
- Background jobs share one controller. Completion refreshes immediately only
  when the page is safe; otherwise the user's active input remains in place and
  a refresh action is shown.
- Browser regression tests cover workflow bindings, receipt editing, card
  matching, stale source state, API failure input retention, and background-job
  completion. JavaScript syntax and distribution contents are also checked.

## Non-goals

- Redesigning the product or changing accounting behavior.
- Requiring a specific JavaScript framework.
- Replacing the local Flask application with a hosted service.
