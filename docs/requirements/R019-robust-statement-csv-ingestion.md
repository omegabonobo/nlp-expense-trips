# R019 — Robust Statement CSV Ingestion

**Priority:** P0
**Status:** Implemented

## Problem

Statement normalization has been validated against a small set of known bank
and card exports. A valid CSV from another provider may use different headers,
encodings, delimiters, preambles, date conventions, signs, or amount/currency
layouts and may be rejected or, worse, interpreted incorrectly.

## Required outcome

The app safely imports a broad range of transaction CSVs, explains how each
file was interpreted, and provides a simple mapping fallback when inference is
ambiguous.

## Functional requirements

1. Detect common CSV encodings, byte-order marks, delimiters, quoting styles,
   preamble rows, repeated headers, and trailing summaries.
2. Infer transaction columns from header aliases and sampled values, including
   date, description, debit, credit, signed amount, purchase amount/currency,
   settlement amount/currency, account/card identifier, and transaction type.
3. Support common date orders, decimal separators, thousands separators,
   currency symbols/codes, parentheses, and debit/credit sign conventions.
4. Validate the inferred mapping against multiple rows and assign confidence
   to the file interpretation.
5. Show a preflight preview with the selected header row, column mapping, date
   convention, sign convention, currencies, accepted row count, skipped rows,
   and warnings.
6. Block reconciliation when required fields or amount signs remain ambiguous;
   never guess silently at low confidence.
7. Let the user correct the header row, column mapping, date convention, and
   sign convention, then preview the result before saving.
8. Save a reusable import profile identified by the file structure rather than
   by a single filename, while requiring review if that structure changes.
9. Preserve the original file, source row, raw values, applied profile, and all
   normalization warnings for audit.
10. Maintain a privacy-safe fixture suite covering multiple providers and
    systematic variants of encoding, delimiter, headers, dates, amounts, and
    currencies.

## Acceptance criteria

- A semantically recognizable CSV from a new provider can import without a
  code change.
- An ambiguous CSV is blocked with an actionable mapping screen rather than
  producing plausible but incorrect transactions.
- Debit, credit, refund, fee, and foreign-currency examples normalize to the
  expected signed purchase and CAD settlement amounts.
- A saved import profile is reused for a matching structure and invalidated
  when its relevant structure changes.
- Existing BNC, AMEX, Wise, standard CSV, XLS, and XLSX behavior remains
  compatible.

## Non-goals

- Claiming support for every arbitrary CSV without user mapping.
- Direct bank or card API connections.
- OCR extraction from statement PDFs.
