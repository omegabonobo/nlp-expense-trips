# NLP Expenses

Local-first CLI for turning trip receipt scans and card statements into an editable CAD expense review workbook.

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

## Reviewing the workbook

The generated workbook has three editable sheets:

- `expense_list`: one row per receipt file.
- `expense_line_items`: extracted receipt line items, including alcohol flags.
- `card_statements`: normalized card statement rows where you can edit `expense_id` to match transactions to receipts.

In `expense_list`, `number_of_person` defaults to `1` and should be reviewed. `corrected_amount_in_currency` is the receipt line-item total minus alcohol, divided by `number_of_person`. `corrected_CAD` is also per person: it applies the CAD statement rate to that corrected per-person amount.

`CAD_amount_statement` is calculated from `card_statements` using the editable `expense_id` column. If it shows `0.00`, no matching statement value has been found yet, and the cell is highlighted red until a matching statement row is assigned.

For alcohol, you can either mark existing line items as `is_alcohol = TRUE`, or edit the automatically created `Alcohol adjustment - manual` line for that receipt. The adjustment line starts at `0.00` and is there so you can enter an alcohol amount when OCR/LLM missed or grouped the drink lines.

## Optional OpenAI fallback

The tool runs OCR and heuristics first. If you want low-confidence receipts to fall back to an OpenAI structured extraction pass, configure a local `.env`:

```bash
.venv/bin/python -m nlp_expenses configure-openai
```

This stores `OPENAI_API_KEY` locally in `.env`, which is ignored by git.
