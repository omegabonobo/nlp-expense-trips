# NLP Expenses

Local-first Mac app and compatible CLI for reviewing trip receipts, reconciling
card statements, finalizing claims, and exporting a synchronized reimbursement
package.

The app separates two decisions that must not be conflated:

- **Claim program:** `arvine_only` or `ivado_sponsored`.
- **Receipt payer:** `employee_personal` or `arvine_corporate_bmo`, with a
  trip default and receipt-level exceptions.

Arvine reimbursement eligibility and IVADO eligibility are also reviewed
separately. For example, alcohol can remain in the employee's Arvine
reimbursement while being explicitly excluded from the IVADO sponsor claim.
The saved `arvine` / `ivado` processing mode remains as an internal compatibility
adapter for extraction, statement handling, and the legacy CLI.

## Local Mac interface

The simplest workflow is to double-click the top-level `NLP Expenses.command`
file in Finder. Do not open `nlp_expenses/templates/index.html`; it is a server
template, not a standalone webpage. On first use, the launcher creates the
local Python environment, installs the project, and opens a private browser
interface whose address starts with `http://127.0.0.1:`.

From the interface you can:

- create or select a business trip;
- see and reveal the exact receipt, statement, and output folders in Finder;
- upload receipt scans and bank/card exports directly;
- organize receipts in any depth of subfolders under `expenses_receipts`; the
  app processes them recursively and keeps their relative paths distinct;
- automatically refresh when receipt or statement files are added, changed, or
  removed directly in Finder, with a manual **Refresh files** fallback;
- edit receipt fields, payer, Arvine/IVADO eligibility, person count, and extracted lines; add/remove manual lines; reactivate or deactivate any item; and correct its alcohol classification;
- validate Arvine statement files before generation;
- sync either mode against normalized statement transactions, review the calculated CAD exchange rate, and save manual mapping overrides;
- correct extracted invoice fields, document manual CAD amounts, resolve duplicates, and split charges/refunds/personal portions;
- review statement coverage against the trip dates and expected cards/accounts;
- enter the traveller plus optional dates/purpose, choose the default receipt payer,
  and review the employee share on each receipt;
- choose receipt extraction once—Best quality with a locally stored OpenAI API key or Basic/offline—and reuse that choice for reconciliation and Excel;
- review a blocking issue queue, per-expense CAD/FX results, and the Arvine accounting preview, then explicitly finalize the current claim;
- generate a compact Arvine report, the shared manifest, and a three-tab IVADO
  workbook when the trip is sponsored, without overwriting earlier manual work;
- open the result in Excel, reveal it in Finder, or download it from the local page;
- lock a reviewed version, export a hashed consolidation ZIP, and archive or restore completed trips.

### Reimbursement report bundle

The UI compiles one canonical reviewed dataset and uses it for every output:

- `expense_review_<trip>_arvine_<timestamp>.xlsx` is always the primary
  reimbursement/accounting report. It contains one readable `Expense Report`
  and the five derived `Accounting Rows` used by the accounting handoff.
- `trip-reimbursement-manifest.v3.ndjson` is the minimal machine-readable shared
  contract consumed by `arvine-accounting-expenses`.
- `expense_review_<trip>_ivado_<timestamp>.xlsx` is added only for
  `ivado_sponsored` trips. It contains exactly `Expense Report`, consolidated
  `Card Statements`, and `Receipt Items`. The first tab mirrors IVADO's
  expense-entry columns; the last tab keeps every extracted item visible,
  including alcohol removed from the IVADO amount.

The lock record hashes the entire generated bundle, not only one workbook.
The exported ZIP includes source receipts/statements, review state, every
generated artifact, and `approval-manifest.json`. The manifest enforces these
controls within a CAD 0.02 tolerance:

- reviewed trip total = employee reimbursement + corporate-paid;
- for sponsored trips, reviewed trip total = IVADO claim + IVADO exclusions;
- receipt detail totals = report totals;
- accounting components = employee reimbursement.

Contract 3.0 contains only receipt/output facts, payer and employee-share
decisions, the Arvine/IVADO CAD amounts, receipt items, and the five accounting
amounts actually consumed downstream. Approver, legal identifiers, settlement
references, policy profiles, and template-confirmation fields are not part of
the handoff.

Closing the launcher Terminal window stops the local interface. No files are uploaded anywhere except when Best quality sends receipt content to the OpenAI API.

### Enabling Best quality

Open the app and click **Add OpenAI key** in the top-right status area. Paste
the key into the local settings dialog. The app stores it at
`<project folder>/.env` as `OPENAI_API_KEY`, applies private `0600`
permissions, and never displays or returns the saved value. Each colleague
must configure a key on their own Mac; copying the application does not copy a
usable key unless they also copy the ignored local `.env` file.
Keys can be created and managed on the
[OpenAI API key page](https://platform.openai.com/api-keys). OpenAI recommends
separate keys for team members and warns against sharing or committing them.

The extraction choice appears only in **Step 3 · Extract and review receipts**.
Best quality generally handles unclear scans, complex layouts, and
foreign-language receipts better, but it sends receipt content to OpenAI,
requires internet access, and may incur API charges. Basic/offline keeps
receipt processing entirely on the Mac and has no API cost, but difficult
scans are more likely to need manual corrections.

Reconciliation and Excel generation inherit the method recorded by the
current receipt scan. Statement parsing, matching logic, and workbook creation
run locally and do not have a separate quality choice.

Until a key is configured, **Best quality** is disabled and Basic/offline is
selected. Advanced users can still run
`.venv/bin/nlp-expenses configure-openai` from the project folder.

### Invoice and card reconciliation

For either mode, use **Sync and auto-match** after uploading receipts and statements. The reconciliation table groups imported statement rows into card transactions and shows:

- the original purchase amount and currency;
- the complete CAD amount charged by the card provider;
- the automatically matched or suggested invoice and confidence;
- the accounting exchange rate in CAD per invoice-currency unit.

Each card transaction can be reassigned to any receipt, explicitly left unmatched,
or restored to the automatic result. Arvine also supports auditable purchase,
refund, fee, personal, and ignored split allocations. Manual choices are saved
in the trip’s local reconciliation metadata and are reapplied to the live app
preview and future generated workbooks. Multiple card transactions may be
mapped to one receipt; their complete CAD amounts are combined when displaying
that receipt’s accounting rate.

The same workflow is callable from Python for scripts or other front ends:

```python
from nlp_expenses.reconciliation import set_manual_match, sync_reconciliation

snapshot = sync_reconciliation(trip_dir, project_root, llm_mode="off")
updated = set_manual_match(trip_dir, transaction_group_id, expense_file="hotel.pdf")
```

### Expense, line-item, and alcohol review

Use **Scan receipts** after uploading receipts. Every extracted line remains
visible with its amount, alcohol classification, confidence, matched evidence,
and an independent `included` choice. The receipt editor is the canonical
record for extracted fields, whole-expense inclusion, number of people,
business purpose, justified manual CAD, and review notes.

- IVADO deactivates confidently detected alcohol automatically.
- Arvine flags detected alcohol but leaves it included by default.
- Reactivating an item does not erase its alcohol classification.
- Receipt, line, included, and excluded totals remain visible together.
- Descriptions and amounts can be corrected; manual lines can be added or
  removed before finalization.
- Receipt content is authoritative for the expense date. When it contains no
  usable date, common dates embedded in the filename are used as a lower-confidence,
  explicitly reviewable fallback; ambiguous filename dates are left unresolved.
- Receipt changes make the stored review stale and require a new scan.

For partial Arvine meals, the workbook retains the full card charge for FX
evidence and applies the included-line percentage proportionally to claimable
CAD, GST/HST, and QST. The same workflow is callable from Python:

```python
from nlp_expenses.line_items import (
    set_line_item_review,
    sync_line_item_review,
)

snapshot = sync_line_item_review(trip_dir, project_root, llm_mode="off")
updated = set_line_item_review(
    trip_dir,
    "restaurant.pdf",
    line_id,
    {"included": True},
)
```

The detailed requirement set, implementation status, and remaining gaps are in
[docs/requirements/README.md](docs/requirements/README.md) and the
[end-to-end implementation review](docs/requirements/IMPLEMENTATION-REVIEW.md).
The real Melbourne Basic/OpenAI comparison and browser acceptance evidence are
in the
[Melbourne end-to-end acceptance report](docs/benchmarks/MELBOURNE-E2E-20260726.md).

You can also launch it from Terminal:

```bash
./scripts/setup_local_env.sh
.venv/bin/nlp-expenses ui
```

## Folder layout

Each trip lives under `trips/` and must use `YYYYMM_tripName`, for example:

```text
trips/
  202606_melbourne/
    expenses_receipts/
    card_statements/
```

## Run

First create the local Python environment:

```bash
./scripts/setup_local_env.sh
```

Generate directly:

```bash
.venv/bin/python -m nlp_expenses generate trips/202606_melbourne
```

Create an Arvine trip and save the mode in its metadata:

```bash
.venv/bin/python -m nlp_expenses create-trip 202607_montreal --mode arvine
```

Existing trips without mode metadata default to IVADO. A saved mode can be overridden for one generation with `--mode ivado` or `--mode arvine`.

The command asks whether to use an OpenAI API key and tells you that output quality is much better with LLM extraction. If you answer `y`, it asks for the key in the terminal and saves it locally in `.env`.
The default model is `gpt-5.2` because it supports image input and Structured Outputs for receipt extraction. Advanced users can override it by manually setting `OPENAI_MODEL` in `.env`.

Run heuristics only without prompting:

```bash
.venv/bin/python -m nlp_expenses generate trips/202606_melbourne --llm off
```

Run with OpenAI extraction forced for every receipt. OCR/native text is still used first, and image-based OpenAI extraction is used when text extraction is empty or the receipt total and line items do not reconcile:

```bash
.venv/bin/python -m nlp_expenses generate trips/202606_melbourne --llm required
```

If `.env` does not already contain `OPENAI_API_KEY`, the command asks for it in the terminal and saves it locally.

The output is written beside the trip folder contents as:

```text
trips/YYYYMM_tripName/expense_review_YYYYMM_tripName.xlsx
```

## Arvine statement workflow

Put any number of raw Amex, BMO, BNC, or Wise exports in the trip's `card_statements/` folder. Compatible generic CSV/XLS/XLSX tables are also accepted. Files are detected from their column signatures, normalized in memory, and left unchanged; no provider-specific CSV is generated.

Before receipt processing, Arvine validates every statement and asks once whether all statements have been added. Answering `No` exits without creating or overwriting the workbook. For scripts or other non-interactive runs, confirm explicitly:

```bash
.venv/bin/python -m nlp_expenses generate trips/202607_montreal --mode arvine --statements-complete --llm off
```

An unsupported or malformed file stops generation and names the affected file. Wise general-history exports import only `CARD_TRANSACTION` activity, retain split funding legs, and leave CAD blank when the exact complete CAD settlement is unavailable.

Arvine creates four sheets:

- `expense_detail`: one row per receipt, tax fields, statement match, manual CAD override, deductible/recoverable calculations, and review statuses.
- `expense_summary`: editable report metadata, the five-line CAD journal, and balance/completeness checks.
- `card_statements`: provider-neutral normalized transactions, funding legs, audit rows, and editable `expense_id` / `match_status` fields.
- `expense_line_items`: visible meal lines, alcohol evidence, and editable classification/inclusion choices that feed the proportional claim formulas.

The Canadian tax-documentation review flags follow the current $100 and $500 invoice-information thresholds described by the [Canada Revenue Agency](https://www.canada.ca/en/revenue-agency/services/tax/businesses/topics/gst-hst-businesses/calculate-prepare-report/input-tax-credit.html) and [Revenu Québec](https://www.revenuquebec.ca/en/businesses/consumption-taxes/gsthst-and-qst/collecting-gst-and-qst/preparing-invoices/). These checks assist review; they are not tax advice and do not determine whether a purchase is taxable or eligible.

## Reviewing an IVADO workbook

Finalize IVADO in the app before exporting. The generated workbook remains a
shareable, editable record with three sheets:

- `expense_list`: one row per receipt file.
- `expense_line_items`: extracted receipt line items with separate `is_alcohol` and `included` choices plus detection evidence.
- `card_statements`: normalized card statement rows where you can edit `expense_id` to match transactions to receipts.

In `expense_list`, `number_of_person` comes from the app review. When the user
has deactivated a positive line, `corrected_amount_in_currency` is the sum of
included positive lines divided by `number_of_person`. Otherwise the reviewed
receipt total remains authoritative, preventing incomplete or synthetic OCR
lines from silently changing the claim. `corrected_CAD` is also per person and
uses the exact card settlement proportionally when the statement purchase
amount is compatible; the workbook shows the accounting basis/status used.

`CAD_amount_statement` is calculated from the mapping finalized in the app and
exported into `card_statements`. If it shows `0.00`, no matching statement value
was finalized. Post-export Excel edits remain possible, but they do not
round-trip into the app.

`is_alcohol` records the classification; `included` controls the calculation.
This means an alcoholic line can be reactivated without relabelling it as
non-alcoholic. The automatically created `Alcohol adjustment - manual` line
remains available when OCR/OpenAI missed or grouped the drink lines.

## Optional OpenAI fallback

The tool runs OCR and heuristics first. If you want low-confidence receipts to fall back to an OpenAI structured extraction pass, configure a local `.env`:

```bash
.venv/bin/python -m nlp_expenses configure-openai
```

This stores `OPENAI_API_KEY` locally in `.env`, which is ignored by git.
