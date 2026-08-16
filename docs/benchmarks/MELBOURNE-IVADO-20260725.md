# Melbourne IVADO benchmark — 2026-07-25

> Historical first-pass result. The key was subsequently corrected and the
> complete Basic, OpenAI, IVADO, and Arvine acceptance is documented in
> [MELBOURNE-E2E-20260726.md](MELBOURNE-E2E-20260726.md).

## Scope

- Historical source trip (read-only): `trips/202606_melbourne`
- Isolated benchmark trip: `trips/202606_melbourne-benchmark`
- Copied inputs: 20 receipt files and one statement file, verified byte-for-byte
- Excluded from the copy: historical workbooks, line-item decisions, mappings,
  lifecycle state, and other generated outputs
- Benchmark operation: one-pass receipt extraction, fresh line-item review,
  IVADO statement matching, and diagnostic workbook creation

The machine had Tesseract available. A value was saved as
`OPENAI_API_KEY`, but the OpenAI API rejected it with HTTP 400. No secret
value was logged or copied.

## Results

| Metric | Basic / offline | Best requested |
|---|---:|---:|
| Status | Complete | Local fallback only |
| Elapsed time | 9.22 s | 16.72 s |
| Receipts | 20 | 20 |
| Date coverage | 19 / 20 | 19 / 20 |
| Vendor coverage | 20 / 20 | 20 / 20 |
| Total coverage | 18 / 20 | 18 / 20 |
| Currency coverage | 18 / 20 | 18 / 20 |
| Meal receipts | 14 | 14 |
| Meaningful extracted lines | 63 | 63 |
| Candidate alcohol lines deactivated | 9 | 9 |
| Meal receipts whose lines reconcile | 11 / 14 | 11 / 14 |
| Meal receipts needing review | 5 | 5 |
| Receipts blocking browser generation | 2 | 2 |
| Statement transactions | 23 | 23 |
| Automatic statement matches | 15 | 15 |
| Statement suggestions | 21 | 21 |
| Successful OpenAI receipts | 0 | 0 |
| Local fallbacks caused by OpenAI failure | 0 | 20 |

This is not yet a valid Basic-versus-OpenAI quality comparison. The
Best-requested workbook is explicitly named `best-fallback` because every
receipt used local extraction after the OpenAI request failed.

Machine-readable details are stored in
`trips/202606_melbourne-benchmark/benchmark_202606_melbourne-benchmark_20260725-105345.json`.

## What the Basic run did well

- Produced a valid three-sheet IVADO workbook with no detected formula-error
  tokens.
- Extracted 19 dates, 18 receipt totals, and 18 currencies from 20 inputs.
- Parsed 23 statement transactions and automatically matched 15.
- Detected the nine Melbourne alcohol candidates evidenced by the historical
  manual workbook:
  - Tiger schooner, Stomping Ground IPA, and OCR'd Asahi at Juni;
  - Strawberry Fields and East Skipper at Farmers Daughters;
  - Balter at Elephant & Wheelbar;
  - Canta, Pisco Sour, and Chardonnay at Reine & La Rue.
- Correctly leaves those classifications editable. IVADO starts them as
  excluded; a user can reactivate any line without deleting its alcohol flag.
- No longer treats `Galician Scotch Filet` as alcohol.
- Dashboard alcohol and exclusion totals no longer count the zero-dollar
  `Alcohol adjustment - manual` placeholders.

## Review and consolidation gaps found

1. **The benchmark source set is incomplete relative to the manual workbook.**
   The manual workbook contains `Scanned_20260601-1736.pdf` (Fancy Hanks), but
   that receipt is not present in the current historical receipt folder.

2. **Two receipts have no extracted total.**
   `Scanned_20260601-breakfast.pdf` and `Scanned_20260604-0752.pdf` require
   manual completion.

3. **Two line-item reviews block browser generation.**
   - Juni has `SALMON SASHIMI` read as AUD 76 instead of AUD 26, so its lines
     exceed the AUD 314 receipt by AUD 50.
   - Farmers Daughters contains a generated AUD 143.10 gap line. Its alcohol
     candidates are now correct, but the unread portion must still be reviewed.

4. **Several top-level fields need correction.**
   - The hotel date is missing and its vendor is `Canada` instead of
     `Meridien Hotel`.
   - Thai Airways is AUD 3,579.43 in Basic versus AUD 3,649.43 in the manual
     workbook.
   - Elephant & Wheelbar is classified as CAD rather than AUD.
   - Uber receipts are classified as flights rather than transport/taxi.
   - One Coles receipt is classified as `other` rather than `meal`.

5. **Statement reconciliation still needs a human pass.**
   Fifteen of 23 rows are auto-matched, but eight remain unassigned. Some are
   expected non-trip rows (Spotify and F1); others are legitimate trip
   candidates with weak or incorrect suggestions (Farmers Daughters, Reine &
   La Rue, Fancy Hanks, Meridien F&B, and Thai Airways).

6. **IVADO browser reconciliation remains a product gap.**
   The generated IVADO workbook permits editing statement `expense_id` values,
   but the browser's richer manual invoice-to-statement mapping workflow is
   currently Arvine-only. Similar Coles transactions also demonstrate why
   unconstrained many-to-one automatic matches require review.

7. **A real Best-quality benchmark remains blocked by credentials.**
   Replace the saved value through the app's **OpenAI settings** dialog with a
   valid key created at `https://platform.openai.com/api-keys`, then rerun:

   ```bash
   .venv/bin/python scripts/benchmark_trip.py \
     trips/202606_melbourne-benchmark --quality best
   ```

   The benchmark script starts from a fresh line-item state and labels a run
   `fallback_only` when no receipt succeeds through OpenAI, preventing local
   fallback output from being mistaken for a Best-quality result.

## Generated artifacts

- `expense_review_202606_melbourne-benchmark_ivado_20260725-105345_basic-benchmark.xlsx`
- `expense_review_202606_melbourne-benchmark_ivado_20260725-105345_best-fallback-benchmark.xlsx`
- `benchmark_20260725-105345_basic_line_items.json`
- `benchmark_20260725-105345_best_line_items.json`
- `benchmark_source_manifest.json`

The historical Melbourne workbook files were not written or renamed.
