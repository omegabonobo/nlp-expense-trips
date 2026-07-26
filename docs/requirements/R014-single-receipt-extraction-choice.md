# R014 — Single Receipt Extraction Choice

**Priority:** P1  
**Status:** Implemented

## Problem

The interface previously exposed Best quality versus Basic/offline during the
receipt line-item scan, Arvine reconciliation, and Excel generation. These
controls all selected the same receipt extraction engine; they were not
separate tools. Repeating the choice made the workflow look more complex and
allowed later stages to request a method inconsistent with the reviewed
receipt snapshot.

## Required outcome

The user chooses a receipt extraction method only when scanning receipts.
That choice is stored with the current receipt review and inherited by
reconciliation and workbook generation.

## Method tradeoff

### Best quality · OpenAI

- intended for unclear scans, complex layouts, and foreign-language receipts;
- requires a locally configured API key and internet access;
- sends receipt content to the OpenAI API;
- may incur API usage charges.

### Basic / offline

- uses Tesseract OCR and local heuristics;
- keeps receipt processing on the Mac;
- requires no API key and creates no API usage charge;
- is more likely to require manual corrections on difficult scans.

## Processing boundaries

- The quality choice affects receipt and invoice fields and receipt line items.
- Card-statement parsing and normalization always run locally.
- Invoice-to-statement matching always runs locally.
- Excel layout, accounting formulas, and file creation always run locally.
- A later stage may reread a source receipt, but it must use the method stored
  by the current receipt scan; the browser cannot override it.
- Changed receipt files make the stored scan stale and block reconciliation
  and generation until the user rescans.

## User-interface requirements

1. Show the two methods and their privacy, connectivity, cost, and likely
   accuracy tradeoffs in Step 3.
2. Disable Best quality when no OpenAI key is configured.
3. In reconciliation, show the inherited receipt method as read-only context.
4. In Excel generation, show the inherited receipt method and state clearly
   that workbook creation is local.
5. Do not show another quality selector in reconciliation or generation.

## Acceptance criteria

- The rendered trip dashboard contains one Best-versus-Basic control.
- Reconciliation is unavailable until a current receipt scan exists.
- Reconciliation ignores any client-supplied quality override and uses the
  stored receipt-scan method.
- Workbook generation ignores any client-supplied quality override and uses
  the stored receipt-scan method.
- A stored Best-quality scan requires the key to remain available for later
  rereads.
- The full automated suite and a rendered browser smoke test pass.
