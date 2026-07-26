# Melbourne end-to-end acceptance — 2026-07-26

## Outcome

The local app completed the full IVADO workflow on an isolated copy of the
Melbourne trip and the full Arvine workflow on a smaller real-data subset.
Both modes were exercised through the running Flask interface in the in-app
browser, not by opening the Jinja template as a local file.

The historical trip at `trips/202606_melbourne` and its manual workbook were
used as read-only reference data. All writes went to these isolated trips:

- `trips/202606_melbourne-benchmark`
- `trips/202606_melbourne-acceptance-basic`
- `trips/202606_melbourne-arvine-acceptance`

## Workflow coverage

| Workflow stage | IVADO acceptance | Arvine acceptance |
|---|---|---|
| Launch and system status | Local-only server, per-launch token, OCR ready, OpenAI key configured | Same |
| Create/select trip and mode | Existing isolated IVADO trip selected | New Arvine trip created in UI |
| Finder collection workflow | Trip, receipt, and statement paths shown; five relevant reveal/open-folder controls visible | Same |
| Direct folder changes | File watcher detected files added outside the UI without a manual refresh | Two receipts and one statement detected in about four seconds |
| Receipt extraction | Basic/offline scan; one extraction choice in Step 3 | Best quality; OpenAI used with no local fallback |
| Receipt correction | Date, vendor, category, currency, total, people, purpose, and manual CAD corrected in UI | Vendor, total, people, purpose corrected |
| Line-item review | 87 visible lines; nine alcohol candidates automatically deactivated; lines added, edited, removed, and reactivated | Alcohol flagged but initially included; Balter line manually deactivated |
| Statement sync | 23 transactions imported; automatic results reviewed and manual mappings saved | Two exact statement transactions imported and mapped |
| Non-trip transactions | Spotify, F1, and an evidenced duplicate Coles charge explicitly excluded with reasons | Not applicable to subset |
| Foreign-currency accounting | Statement purchase amount, exact CAD settlement, observed FX, person split, and fallback basis visible | Same |
| Completeness | Trip metadata completed; no blocking issues at finalization | Explicit subset coverage acknowledgement recorded |
| App-first review | Final CAD result reviewed in the browser before export | CAD and accounting/tax preview reviewed before export |
| Excel export | Five versioned workbooks retained; newest created from the final code | Versioned four-sheet Arvine workbook retained |
| Approval package | Workbook ready for approval | Approved as `Test Reviewer`; ZIP and manifest generated and validated |

The browser smoke test also confirmed that Best quality is available when the
local key is configured, Basic/offline remains available, and Excel creation
inherits the Step 3 extraction method rather than asking a second quality
question.

## Melbourne extraction benchmark

Both benchmarks used the same isolated set of 20 source receipts and one
statement. They are fresh automatic extraction runs without manual field or
line corrections, so the counts measure the review burden rather than the
quality of the final corrected claim.

| Metric | Basic/offline | Best quality |
|---|---:|---:|
| Elapsed time | 9.68 s | 134.19 s |
| OpenAI receipts / local fallbacks | 0 / 0 | 20 / 0 |
| Date coverage | 19 / 20 | 20 / 20 |
| Vendor coverage | 20 / 20 | 20 / 20 |
| Total coverage | 18 / 20 | 20 / 20 |
| Currency coverage | 18 / 20 | 20 / 20 |
| Meaningful receipt lines | 63 | 69 |
| Synthetic balancing/review lines | 10 | 8 |
| Alcohol candidates | 9 | 10 |
| Automatically deactivated alcohol | 9 | 9 |
| Meal receipts whose lines reconcile | 11 / 14 | 10 / 12 |
| Meal receipts needing line review | 5 | 2 |
| Receipts blocking browser generation | 2 | 0 |
| Statement automatic matches | 15 / 23 | 19 / 23 |
| Statement suggestions | 21 / 23 | 21 / 23 |

Best quality materially improved top-level field completeness and automatic
statement matching, and removed the initial browser-generation blockers. It is
slower, requires internet, sends receipt content to OpenAI, and remains
nondeterministic: the final run had full top-level coverage but two small meal
receipts still needed line-total review; an earlier successful run missed one
currency. The UI review remains mandatory for either method.

Machine-readable reports:

- `trips/202606_melbourne-benchmark/benchmark_202606_melbourne-benchmark_20260726-022809.json`
- `trips/202606_melbourne-benchmark/benchmark_202606_melbourne-benchmark_20260726-022833.json`

## Final IVADO result

The IVADO acceptance trip includes the missing Fancy Hanks receipt, for 21
receipts total. Corrections and reconciliation were performed in the UI. The
final browser preview was CAD 7,532.51. After LibreOffice recalculated the
newest workbook, the unrounded formula sum was CAD 7,532.501980, compared with
CAD 7,532.491980 in the historical manual workbook. The CAD 0.01 unrounded
difference is below the displayed-cent precision and comes from the app's
explicit statement-backed FX calculations.

The newest workbook is:

`trips/202606_melbourne-acceptance-basic/expense_review_202606_melbourne-acceptance-basic_ivado_20260726-023147.xlsx`

Checks on the recalculated copy:

- no Excel formula error values;
- corrected hotel ID uses `20260605`, not a missing-date placeholder;
- Juni CAD 83.865955, Farmers Daughters CAD 106.826305, and Reine & La Rue
  CAD 153.995643 after alcohol and person splits;
- exact full-card settlements retained as FX evidence;
- malformed Thai statement purchase amount explicitly uses
  `receipt_fallback_mismatch` while retaining CAD 3,688.57;
- receipt-total fallbacks are labelled `receipt total used`, not `missing`;
- earlier generated workbooks remain present and unchanged.

## Final Arvine result

The Arvine acceptance trip used two real Melbourne receipts and a statement
subset containing their two real settlement rows. The final app and
LibreOffice-recalculated workbook agree:

- Juni Restaurant: CAD 108.37 traveller share;
- Elephant & Wheelbarrow: CAD 35.82 after the reviewed alcohol exclusion;
- shareholder reimbursement and journal debits: CAD 144.19;
- journal balance difference: CAD 0.00;
- no formula error values.

Workbook:

`trips/202606_melbourne-arvine-acceptance/expense_review_202606_melbourne-arvine-acceptance_arvine_20260726-020750.xlsx`

Approved package:

`trips/202606_melbourne-arvine-acceptance/trip_package_202606_melbourne-arvine-acceptance_20260726-020839.zip`

The ZIP passed an integrity test and contains the two receipts, statement,
reconciliation state, line-item state, current workbook, trip configuration,
and manifest. The manifest records approval metadata, input/review summaries,
and the workbook hash. The local `.env` and OpenAI key are not included.

## Defects found and corrected during the loop

1. Any IVADO statement transaction can now be excluded/restored with an audit
   reason; this is no longer limited to Arvine allocation behavior.
2. Shared meal calculations no longer divide an already per-person card
   settlement twice.
3. Exact CAD settlement is prorated by reviewed line/person ratios while the
   full card charge remains visible as FX evidence.
4. Malformed or wrong-currency statement purchase amounts fall back visibly
   instead of producing a misleading accounting rate.
5. When there is no positive excluded line, the reviewed receipt total remains
   authoritative over stale/synthetic line sums.
6. Resolved automatic warnings are removed from final review notes.
7. Explicit line choices and manually added lines survive small wording
   changes across OpenAI rescans.
8. Corrected receipt dates now update generated expense IDs.
9. Receipt-total fallbacks no longer appear as `missing` in Excel.
10. A bare percentage is no longer treated as alcohol; tax, fee, surcharge,
    gratuity, tip, and discount rows override an erroneous OpenAI alcohol flag.
11. Integration tests copy the Melbourne fixture to a temporary trip instead
    of generating into the historical source trip.
12. Unmapped statement rows now leave IVADO CAD/FX cells blank instead of
    producing spreadsheet-engine-dependent `#VALUE!` errors.

## Automated and structural verification

- Full suite: **106 tests passed**.
- Python bytecode compilation: passed.
- Browser JavaScript syntax check: passed.
- Launcher shell syntax and CLI help: passed.
- Flask test-client coverage includes trip creation, safe paths, upload
  deduplication/collisions, removal, statement validation, key-disabled
  quality controls, generation gating, progress, downloads, Finder/open target
  restrictions, approval, and failure cleanup.
- Workbooks were inspected structurally, visually rendered, recalculated with
  LibreOffice, and scanned for formula-error values.
- The approved ZIP passed `ZipFile.testzip()` and manifest inspection.

## Remaining human or product gaps

1. **Extraction still needs review.** Best quality reduced blockers but did not
   eliminate missing currency or small line-total discrepancies, and results
   can vary across runs.
2. **Statement completeness cannot be proven from files alone.** The app shows
   date/account coverage and requires an acknowledgement, but the user must
   know whether every relevant export was supplied.
3. **Similar legitimate transactions remain ambiguous.** The UI supports
   manual mapping/exclusion and warns about duplicates, but merchant/date/amount
   similarity cannot always determine intent.
4. **Accounting and policy settings require company validation.** The workbook
   records the applied Arvine assumptions; it does not provide legal or tax
   advice.
5. **Detailed line review is meal-focused.** Hotel folios, airfare components,
   mileage, and per-diem claim entry remain total-level or deferred.
6. **Excel edits do not round-trip.** Excel is a shareable final artifact; the
   app remains the canonical reviewed state before export.
7. **OS integration needs a physical Mac click-through.** Open/reveal routes
   and safe target restrictions are automated, and the live controls were
   visible in browser tests. This run intentionally did not launch Finder or
   Excel repeatedly. A colleague acceptance pass should double-click the
   launcher and click each OS action once.
