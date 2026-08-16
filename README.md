# NLP Expenses

Local-first Mac app and compatible CLI for reviewing trip receipts, reconciling
card statements, finalizing claims, and exporting a synchronized reimbursement
package.

## Install on a Mac

Install Python 3.11 or newer plus Tesseract OCR, then clone this repository or
download the latest GitHub release. In Finder, double-click
`NLP Expenses.command`; the first launch creates the local environment and opens
the app in your browser.

Fresh installs keep private settings and trip files outside the source under
`~/Documents/NLP Expenses Data/`. This makes Git updates and replacement source
ZIPs safe for local data. Existing checkouts with a `.env` or `trips/` directory
retain their original data location.

See the concise [Mac getting-started guide](docs/GETTING_STARTED.md) for
installation, updates, privacy boundaries, and troubleshooting. The source is
available under the [MIT License](LICENSE); receipts, statements, generated
reports, exported packages, and API keys must never be committed.
The bundled IVADO form remains subject to its original owner's terms; see the
[third-party notice](THIRD_PARTY_NOTICES.md).

For a new contributor, start with the
[development and packaging guide](docs/DEVELOPMENT.md). It covers the supported
Python versions, quality checks, distribution build, private-data boundaries,
and the wheel smoke test.

The app separates two decisions that must not be conflated:

- **Reimbursement program:** `company_reimbursed` or `ivado_reimbursed`.
- **Payment source:** `traveller_personal` or `company_card`, with a
  trip default and receipt-level exceptions.

Company-report inclusion and IVADO eligibility are reviewed separately. For
example, alcohol remains in the company expense record while being explicitly
excluded from the IVADO claim. Older saved values are normalized automatically;
the versioned v3 export retains a compatibility adapter for its existing consumer.

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
- edit receipt fields, payment source, company/IVADO eligibility, person count, and extracted lines; add/remove manual lines; reactivate or deactivate any item; and correct its alcohol classification;
- validate company-trip statement files before generation;
- sync either mode against provider-neutral statement transactions, apply cached weekly CAD rates when an exact CAD amount is absent, review the conversion source, and save manual mapping overrides;
- correct extracted invoice fields, document manual CAD amounts, resolve duplicates, and split charges/refunds/personal portions;
- review statement coverage against the trip dates and expected cards/accounts;
- enter the traveller plus optional dates/purpose, choose the default receipt payer,
  and review the traveller share on each receipt;
- choose receipt extraction once—Best quality with a locally stored OpenAI API key or Basic/offline—and reuse that choice for reconciliation and Excel;
- review a blocking issue queue, per-expense CAD/FX results, and the company accounting preview, then explicitly finalize the current claim;
- generate a compact company report, the shared manifest, and a three-tab IVADO
  workbook when the trip is sponsored, without overwriting earlier manual work;
- open the result in Excel, reveal it in Finder, or download it from the local page;
- lock a reviewed version, export a hashed consolidation ZIP, and archive or restore completed trips.

### Reimbursement report bundle

The UI compiles one canonical reviewed dataset and uses it for every output:

- `expense_review_<trip>_company_<timestamp>.xlsx` is always the primary
  reimbursement/accounting report. It contains one readable `Expense Report`
  and the five derived `Accounting Rows` used by the accounting handoff.
- `trip-reimbursement-manifest.v3.ndjson` is the minimal machine-readable shared
  contract consumed by the accounting handoff.
- `expense_review_<trip>_ivado_<timestamp>.xlsx` is added only for
  `ivado_reimbursed` trips. It contains exactly `Expense Report`, consolidated
  `Card Statements`, and `Receipt Items`. The first tab mirrors IVADO's
  expense-entry columns; the last tab keeps every extracted item visible,
  including alcohol removed from the IVADO amount.

The lock record hashes the entire generated bundle, not only one workbook.
The exported ZIP includes source receipts/statements, review state, every
generated artifact, and `approval-manifest.json`. The manifest enforces these
controls within a CAD 0.02 tolerance:

- reviewed trip total = traveller reimbursement + company-paid + program exclusions;
- for sponsored trips, reviewed trip total = IVADO claim + IVADO exclusions;
- for sponsored trips, traveller reimbursement + company-paid = IVADO claim;
- receipt detail totals = report totals;
- accounting components = traveller reimbursement.

Contract 3.0 contains only receipt/output facts, payer and employee-share
decisions, the company/IVADO CAD amounts, receipt items, and the five accounting
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
or restored to the automatic result. Company mode also supports auditable purchase,
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
- The company report retains alcohol while IVADO excludes it.
- Reactivating an item does not erase its alcohol classification.
- Receipt, line, included, and excluded totals remain visible together.
- Descriptions and amounts can be corrected; manual lines can be added or
  removed before finalization.
- Receipt content is authoritative for the expense date. When it contains no
  usable date, common dates embedded in the filename are used as a lower-confidence,
  explicitly reviewable fallback; ambiguous filename dates are left unresolved.
- Receipt changes make the stored review stale and require a new scan.

For partially eligible meals, the company workbook retains the full card charge for FX
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

When the application is installed outside this checkout, run it from the data
directory that should contain `trips/` and `.env`, or select that directory
explicitly:

```bash
nlp-expenses --root ~/Documents/ivado-expenses ui
```

`NLP_EXPENSES_ROOT` provides the same default for scripts. CLI flags such as
`--root` and `--version` come before the subcommand.

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

Create an own-company reimbursement trip and save the mode in its metadata:

```bash
.venv/bin/python -m nlp_expenses create-trip 202607_montreal --mode company
```

Existing trips without mode metadata default to IVADO. A saved mode can be overridden for one generation with `--mode ivado` or `--mode company`.

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

## Company statement workflow

Put any number of raw Amex, BMO, BNC, or Wise exports in the trip's `card_statements/` folder. Compatible generic CSV/XLS/XLSX tables are also accepted. Files are detected from their column signatures, normalized in memory, and left unchanged; no provider-specific CSV is generated.

Before receipt processing, company mode validates every statement and asks once whether all statements have been added. Answering `No` exits without creating or overwriting the workbook. For scripts or other non-interactive runs, confirm explicitly:

```bash
.venv/bin/python -m nlp_expenses generate trips/202607_montreal --mode company --statements-complete --llm off
```

An unsupported or malformed file stops generation and names the affected file.
Wise general-history exports import only `CARD_TRANSACTION` activity and retain
split funding legs. The purchase amount/currency comes from Wise's target
fields. Exact CAD purchase or funding amounts take precedence; otherwise the
shared weekly FX layer converts the target currency to CAD and caches the
source evidence. Wise's exported exchange-rate field is not used for that
fallback.

If a bank export is not recognized, download the standard CSV template from the
Statements card. Its required columns are `transaction_date`, `description`,
`purchase_amount`, and `purchase_currency`. Dates use `YYYY-MM-DD`; positive
amounts are purchases and negative amounts are refunds. Optional `cad_amount`
holds an exact CAD settlement, while `transaction_type`, `posted_date`,
`account`, `cardholder`, and `category` provide audit detail.

Company mode creates four sheets:

- `expense_detail`: one row per receipt, tax fields, statement match, manual CAD override, deductible/recoverable calculations, and review statuses.
- `expense_summary`: editable report metadata, the five-line CAD journal, and balance/completeness checks.
- `card_statements`: provider-neutral normalized transactions, funding legs, audit rows, and editable `expense_id` / `match_status` fields.
- `expense_line_items`: visible meal lines and IVADO classification/removal
  evidence. Own-company trips reimburse the full reviewed share; IVADO trips
  reimburse only the IVADO-eligible share.

The Canadian tax-documentation review flags follow the current $100 and $500 invoice-information thresholds described by the [Canada Revenue Agency](https://www.canada.ca/en/revenue-agency/services/tax/businesses/topics/gst-hst-businesses/calculate-prepare-report/input-tax-credit.html) and [Revenu Québec](https://www.revenuquebec.ca/en/businesses/consumption-taxes/gsthst-and-qst/collecting-gst-and-qst/preparing-invoices/). These checks assist review; they are not tax advice and do not determine whether a purchase is taxable or eligible.

## Reviewing an IVADO workbook

Finalize IVADO in the app before exporting. IVADO mode generates both the
normal company expense workbook and a five-sheet IVADO workbook:

- `modèle - Template FR EN`: IVADO's official expense-report template,
  populated from the reviewed trip while retaining its formulas and layout.
- `Card Statements`: only statement transactions mapped to trip receipts, with
  exact CAD, statement basis, receipt, and allocated claim/removal amounts.
- `Reconciliation`: a compact receipt-level schedule showing full receipt
  totals, people sharing, gross card share, FX, alcohol removed, IVADO claim,
  and traveller reimbursement. Detailed line-item evidence is available behind
  the disabled `IVADO_INCLUDE_DETAILED_RECONCILIATION` code flag.
- `Directives & instructions - FR` and `Guidelines & Instructions - EN`:
  preserved from the official template.

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

For IVADO, `is_alcohol = true` always means the line is removed from both the
sponsor claim and traveller reimbursement with reason `alcohol`. To include a
false positive, correct the line to non-alcoholic. Company-only reimbursement
does not use alcohol classification or line exclusions: every uploaded receipt
remains fully included. The automatically created
`Alcohol adjustment - manual` line remains available in IVADO when OCR/OpenAI
missed or grouped the drink lines.

## Optional OpenAI fallback

The tool runs OCR and heuristics first. If you want low-confidence receipts to fall back to an OpenAI structured extraction pass, configure a local `.env`:

```bash
.venv/bin/python -m nlp_expenses configure-openai
```

This stores `OPENAI_API_KEY` locally in `.env`, which is ignored by git.
