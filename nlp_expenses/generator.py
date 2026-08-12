from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from nlp_expenses.config import (
    ask_openai_for_run,
    get_openai_settings,
    prompt_for_openai_if_missing,
)
from nlp_expenses.extraction.arvine import parse_arvine_receipt
from nlp_expenses.extraction.receipts import parse_receipt
from nlp_expenses.extraction.text import supported_receipt_extensions
from nlp_expenses.fx_rates import WeeklyCadFxResolver
from nlp_expenses.line_items import apply_line_item_review
from nlp_expenses.models import Expense, GenerationProgress, NormalizationResult
from nlp_expenses.statement_normalizer import (
    StatementNormalizationError,
    list_statement_files,
    normalize_statement_files,
    preflight_statement_files,
)
from nlp_expenses.trip_metadata import apply_trip_metadata_defaults
from nlp_expenses.trips import (
    TRIP_MODES,
    list_receipt_files,
    relative_source_name,
    trip_mode,
    trip_receipts_dir,
    trip_statements_dir,
)
from nlp_expenses.workbook import (
    build_arvine_workbook,
    build_ivado_claim_workbook,
    build_reimbursement_report_workbook,
    build_workbook,
)

SUPPORTED_RECEIPTS = supported_receipt_extensions()
LLM_MODES = {"ask", "auto", "off", "required"}

ProgressCallback = Callable[[GenerationProgress], None]
WarningCallback = Callable[[str], None]


def resolve_run_settings(
    root: Path,
    llm_mode: str,
    allow_openai_prompt: bool,
) -> tuple[str | None, str, bool, bool]:
    if llm_mode not in LLM_MODES:
        choices = ", ".join(sorted(LLM_MODES))
        raise ValueError(f"Unknown LLM mode {llm_mode!r}; choose one of: {choices}.")
    if llm_mode in {"ask", "auto"}:
        if allow_openai_prompt:
            api_key, model = ask_openai_for_run(root)
        else:
            api_key, model = get_openai_settings(root)
    elif llm_mode == "required":
        api_key, model = get_openai_settings(root)
        if not api_key:
            if not allow_openai_prompt:
                raise ValueError("Best quality requires an OpenAI API key configured in Settings.")
            api_key, model = prompt_for_openai_if_missing(root)
    else:
        api_key, model = get_openai_settings(root)
    use_llm = llm_mode != "off" and bool(api_key)
    force_llm = llm_mode == "required" or (llm_mode in {"ask", "auto"} and bool(api_key))
    return api_key, model, use_llm, force_llm


def extract_trip_expenses(
    receipts_dir: Path,
    root: Path,
    selected_mode: str,
    llm_mode: str,
    progress_callback: ProgressCallback | None = None,
    warning_callback: WarningCallback | None = None,
    allow_openai_prompt: bool = True,
) -> list[Expense]:
    _api_key, model, use_llm, force_llm = resolve_run_settings(root, llm_mode, allow_openai_prompt)
    receipt_files = [
        path
        for path in list_receipt_files(receipts_dir)
        if path.suffix.lower() in SUPPORTED_RECEIPTS
    ]
    receipt_parser = parse_arvine_receipt if selected_mode == "arvine" else parse_receipt
    expenses: list[Expense] = []
    for index, path in enumerate(receipt_files, start=1):
        source_name = relative_source_name(receipts_dir, path)
        if progress_callback:
            progress_callback(
                GenerationProgress(
                    stage="receipts",
                    current=index,
                    total=len(receipt_files),
                    message=f"Processing receipt {index} of {len(receipt_files)}: {source_name}",
                )
            )
        expense = receipt_parser(path, use_llm=use_llm, model=model, force_llm=force_llm)
        expense.source_file = Path(source_name)
        if use_llm and force_llm and "OpenAI" not in expense.review_note:
            message = (
                f"{source_name}: OpenAI extraction was unavailable; local extraction was used."
            )
            expense.review_note = append_note(expense.review_note, message)
            if warning_callback:
                warning_callback(message)
        expenses.append(expense)
    assign_simple_expense_ids(expenses)
    return expenses


def generate_review(
    trip_dir: Path,
    root: Path,
    llm_mode: str = "ask",
    mode: str | None = None,
    statements_complete: bool = False,
    confirm_input: Callable[[str], str] | None = None,
    output_path: Path | None = None,
    progress_callback: ProgressCallback | None = None,
    warning_callback: WarningCallback | None = None,
    allow_openai_prompt: bool = True,
    contract_bundle: bool = False,
) -> Path | None:
    (
        trip_dir,
        root,
        receipts_dir,
        statements_dir,
        selected_mode,
        output_path,
    ) = validate_generation_request(trip_dir, root, mode, output_path)
    normalization, proceed = prepare_statement_review(
        trip_dir,
        statements_dir,
        selected_mode,
        statements_complete,
        confirm_input,
        progress_callback,
        warning_callback,
    )
    if not proceed:
        return None

    expenses = prepare_reviewed_expenses(
        trip_dir,
        receipts_dir,
        root,
        selected_mode,
        llm_mode,
        progress_callback,
        warning_callback,
        allow_openai_prompt,
    )
    emit_generation_progress(
        progress_callback,
        "workbook",
        0,
        1,
        "Building the reimbursement report bundle",
    )
    apply_arvine_reconciliation(trip_dir, selected_mode, normalization, expenses)

    if contract_bundle:
        result = build_contract_review_bundle(trip_dir, root, output_path)
        complete_message = "Reimbursement report bundle ready"
    else:
        result = build_standard_review_workbook(
            trip_dir,
            root,
            statements_dir,
            selected_mode,
            normalization,
            expenses,
            output_path,
        )
        complete_message = "Workbook ready"

    emit_generation_progress(progress_callback, "complete", 1, 1, complete_message)
    return result


def validate_generation_request(
    trip_dir: Path,
    root: Path,
    mode: str | None,
    output_path: Path | None,
) -> tuple[Path, Path, Path, Path, str, Path | None]:
    trip_dir = trip_dir.resolve()
    root = root.resolve()
    receipts_dir = trip_receipts_dir(trip_dir)
    statements_dir = trip_statements_dir(trip_dir)
    if not receipts_dir.exists():
        raise FileNotFoundError(f"Missing receipts folder: {receipts_dir}")
    statements_dir.mkdir(parents=True, exist_ok=True)

    selected_mode = (mode or trip_mode(trip_dir)).lower()
    if selected_mode not in TRIP_MODES:
        raise ValueError(f"Unknown trip mode: {selected_mode}.")

    if output_path is not None:
        output_path = output_path.resolve()
        if output_path.parent != trip_dir:
            raise ValueError("The output workbook must be created inside the selected trip folder.")
    return trip_dir, root, receipts_dir, statements_dir, selected_mode, output_path


def prepare_statement_review(
    trip_dir: Path,
    statements_dir: Path,
    selected_mode: str,
    statements_complete: bool,
    confirm_input: Callable[[str], str] | None,
    progress_callback: ProgressCallback | None,
    warning_callback: WarningCallback | None,
) -> tuple[NormalizationResult | None, bool]:
    if selected_mode != "arvine":
        return None, True

    statement_files = list_statement_files(statements_dir)
    emit_generation_progress(
        progress_callback,
        "statements",
        0,
        len(statement_files),
        "Validating card and bank statements",
    )
    preflight = preflight_statement_files(statement_files)
    preflight_errors = [error for report in preflight for error in report.errors]
    if preflight_errors:
        raise StatementNormalizationError("\n".join(preflight_errors))
    if not statements_complete:
        prompt = (
            f"Detected {len(statement_files)} statement files in {statements_dir}. "
            "Have all card/bank statements for this trip been added? [y/N]: "
        )
        answer = (confirm_input or input)(prompt).strip().lower()
        if answer not in {"y", "yes"}:
            return None, False

    normalization = normalize_statement_files(
        statement_files,
        fx_resolver=WeeklyCadFxResolver(trip_dir),
    )
    if normalization.errors:
        raise StatementNormalizationError("\n".join(normalization.errors))
    for message in normalization.warnings:
        emit_generation_warning(warning_callback, message)
    emit_generation_progress(
        progress_callback,
        "statements",
        len(statement_files),
        len(statement_files),
        "Statements validated",
    )

    from nlp_expenses.reconciliation import ensure_reconciliation_ready

    ensure_reconciliation_ready(trip_dir)
    return normalization, True


def prepare_reviewed_expenses(
    trip_dir: Path,
    receipts_dir: Path,
    root: Path,
    selected_mode: str,
    llm_mode: str,
    progress_callback: ProgressCallback | None,
    warning_callback: WarningCallback | None,
    allow_openai_prompt: bool,
) -> list[Expense]:
    expenses = extract_trip_expenses(
        receipts_dir,
        root,
        selected_mode,
        llm_mode,
        progress_callback=progress_callback,
        warning_callback=warning_callback,
        allow_openai_prompt=allow_openai_prompt,
    )
    apply_trip_metadata_defaults(trip_dir, expenses)
    apply_line_item_review(trip_dir, expenses)
    # Receipt review can correct an extracted date. Keep the stable-looking
    # workbook IDs aligned with the reviewed, canonical receipt data.
    assign_simple_expense_ids(expenses)
    return expenses


def apply_arvine_reconciliation(
    trip_dir: Path,
    selected_mode: str,
    normalization: NormalizationResult | None,
    expenses: list[Expense],
) -> None:
    if selected_mode != "arvine":
        return

    from nlp_expenses.reconciliation import (
        apply_reconciliation_overrides,
        apply_transaction_decisions,
        load_transaction_decisions,
    )

    apply_reconciliation_overrides(trip_dir, expenses)
    apply_transaction_decisions(
        normalization.transactions if normalization else [],
        load_transaction_decisions(trip_dir),
    )


def build_standard_review_workbook(
    trip_dir: Path,
    root: Path,
    statements_dir: Path,
    selected_mode: str,
    normalization: NormalizationResult | None,
    expenses: list[Expense],
    output_path: Path | None,
) -> Path:
    from nlp_expenses.lifecycle import record_generated_workbook

    if selected_mode == "arvine":
        from nlp_expenses.accounting import trip_accounting_profile
        from nlp_expenses.reconciliation import (
            load_manual_matches,
            load_transaction_allocations,
        )

        result = build_arvine_workbook(
            trip_dir,
            expenses,
            normalization.transactions if normalization else [],
            output_path=output_path,
            manual_matches=load_manual_matches(trip_dir),
            accounting_profile=trip_accounting_profile(root, trip_dir),
            transaction_allocations=load_transaction_allocations(trip_dir),
        )
    else:
        from nlp_expenses.extraction.statements import parse_all_statements
        from nlp_expenses.reconciliation import (
            ivado_statement_transactions_from_reconciliation,
            reconciliation_is_fresh,
        )

        reviewed_transactions = ivado_statement_transactions_from_reconciliation(trip_dir, expenses)
        use_reviewed_mappings = reconciliation_is_fresh(trip_dir)
        transactions = (
            reviewed_transactions if use_reviewed_mappings else parse_all_statements(statements_dir)
        )
        result = build_workbook(
            trip_dir,
            expenses,
            transactions,
            output_path=output_path,
            pre_matched=use_reviewed_mappings,
        )
    record_generated_workbook(root, trip_dir, result)
    return result


def build_contract_review_bundle(
    trip_dir: Path,
    root: Path,
    output_path: Path | None,
) -> Path:
    from nlp_expenses.consolidation import consolidation_view
    from nlp_expenses.lifecycle import record_generated_bundle
    from nlp_expenses.trip_manifest import (
        CONTRACT_FILENAME,
        build_trip_manifest_records,
        write_trip_manifest,
    )

    view = consolidation_view(root, trip_dir)
    records = build_trip_manifest_records(root, trip_dir, view=view)
    primary_path = output_path or trip_dir / f"expense_review_{trip_dir.name}_arvine.xlsx"
    manifest_path = trip_dir / CONTRACT_FILENAME
    created: list[Path] = []
    try:
        result = build_reimbursement_report_workbook(trip_dir, records, primary_path)
        created.append(result)
        manifest = write_trip_manifest(root, trip_dir, view=view, output_path=manifest_path)
        created.append(manifest)
        if view["claim_program"] == "ivado_sponsored":
            ivado_report = build_ivado_claim_workbook(
                trip_dir,
                records,
                ivado_output_path(primary_path),
            )
            created.append(ivado_report)
        record_generated_bundle(root, trip_dir, result, created)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return result


def emit_generation_progress(
    callback: ProgressCallback | None,
    stage: str,
    current: int,
    total: int,
    message: str,
) -> None:
    if callback:
        callback(GenerationProgress(stage=stage, current=current, total=total, message=message))


def emit_generation_warning(callback: WarningCallback | None, message: str) -> None:
    if callback:
        callback(message)


def assign_simple_expense_ids(expenses: list[Expense]) -> None:
    for idx, expense in enumerate(expenses, start=1):
        date_part = expense.date.replace("-", "") if expense.date else "yyyymmdd"
        expense.expense_id = f"{date_part}_#{idx}"


def append_note(existing: str, note: str) -> str:
    return f"{existing.rstrip()} {note}".strip() if existing else note


def ivado_output_path(primary_path: Path) -> Path:
    marker = "_arvine_"
    if marker in primary_path.stem:
        stem = primary_path.stem.replace(marker, "_ivado_", 1)
    else:
        stem = f"{primary_path.stem}_ivado"
    return primary_path.with_name(f"{stem}.xlsx")
