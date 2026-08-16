from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from nlp_expenses.reconciliation_state import load_reconciliation_state
from nlp_expenses.trip_metadata import trip_metadata


def statement_coverage_view(trip_dir: Path, state: dict | None = None) -> dict:
    state = state if state is not None else load_reconciliation_state(trip_dir)
    groups = state.get("transactions", []) if state else []
    metadata = trip_metadata(trip_dir)
    expected = metadata["expected_accounts"]
    trip_start = date.fromisoformat(metadata["start_date"]) if metadata["start_date"] else None
    trip_end = date.fromisoformat(metadata["end_date"]) if metadata["end_date"] else None
    buffer_days = metadata["policy"]["statement_coverage_buffer_days"]
    coverage_start = trip_start - timedelta(days=buffer_days) if trip_start else None
    coverage_end = trip_end + timedelta(days=buffer_days) if trip_end else None
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for group in groups:
        provider = str(group.get("provider") or "unknown")
        account = str(group.get("account_label") or "Unlabelled account")
        grouped[(provider, account)].append(group)

    accounts = []
    present_labels: set[str] = set()
    for (provider, account), account_groups in sorted(grouped.items()):
        dates = sorted(
            str(group["transaction_date"])
            for group in account_groups
            if group.get("transaction_date")
        )
        files = sorted(
            {
                str(source.get("file"))
                for group in account_groups
                for source in group.get("source_rows", [])
                if source.get("file")
            }
        )
        file_ranges: dict[str, list[str]] = defaultdict(list)
        for group in account_groups:
            if not group.get("transaction_date"):
                continue
            for source in group.get("source_rows", []):
                if source.get("file"):
                    file_ranges[str(source["file"])].append(str(group["transaction_date"]))
        normalized_ranges = [
            (filename, min(values), max(values))
            for filename, values in file_ranges.items()
            if values
        ]
        overlaps = any(
            left_start <= right_end and right_start <= left_end
            for index, (_left_file, left_start, left_end) in enumerate(normalized_ranges)
            for _right_file, right_start, right_end in normalized_ranges[index + 1 :]
        )
        unresolved_duplicates = sum(
            1
            for group in account_groups
            if group.get("possible_duplicate") and group.get("duplicate_resolution") == "unresolved"
        )
        label = f"{provider.upper()} {account}".strip()
        present_labels.update({label.casefold(), account.casefold(), provider.casefold()})
        accounts.append(
            {
                "provider": provider,
                "account_label": account,
                "label": label,
                "source_files": files,
                "earliest_date": dates[0] if dates else None,
                "latest_date": dates[-1] if dates else None,
                "transaction_count": len(account_groups),
                "match_eligible_count": sum(
                    1
                    for group in account_groups
                    if group.get("match_eligible") and not group.get("ignored")
                ),
                "unresolved_duplicate_count": unresolved_duplicates,
                "overlapping_files": overlaps,
            }
        )

    gaps = [
        {
            "code": "missing_expected_account",
            "message": f"Expected card/account was not found: {expected_account}.",
        }
        for expected_account in expected
        if expected_account.casefold() not in present_labels
    ]
    for account in accounts:
        if account["overlapping_files"] and account["unresolved_duplicate_count"]:
            gaps.append(
                {
                    "code": "overlapping_exports",
                    "message": (
                        f"{account['label']} has overlapping exports and "
                        f"{account['unresolved_duplicate_count']} unresolved possible duplicate(s)."
                    ),
                }
            )
        if coverage_start and coverage_end:
            earliest = (
                date.fromisoformat(account["earliest_date"]) if account["earliest_date"] else None
            )
            latest = date.fromisoformat(account["latest_date"]) if account["latest_date"] else None
            if not earliest or not latest:
                gaps.append(
                    {
                        "code": "missing_account_dates",
                        "message": f"{account['label']} does not provide usable dates for trip coverage.",
                    }
                )
            elif latest < coverage_start or earliest > coverage_end:
                gaps.append(
                    {
                        "code": "no_trip_overlap",
                        "message": (
                            f"{account['label']} transaction dates do not overlap the expected "
                            f"{coverage_start.isoformat()} to {coverage_end.isoformat()} trip window."
                        ),
                    }
                )
            else:
                if earliest > coverage_start:
                    gaps.append(
                        {
                            "code": "coverage_starts_late",
                            "message": (
                                f"{account['label']} starts on {earliest.isoformat()}, after the expected "
                                f"coverage start {coverage_start.isoformat()}."
                            ),
                        }
                    )
                if latest < coverage_end:
                    gaps.append(
                        {
                            "code": "coverage_ends_early",
                            "message": (
                                f"{account['label']} ends on {latest.isoformat()}, before the expected "
                                f"coverage end {coverage_end.isoformat()}."
                            ),
                        }
                    )
    return {
        "accounts": accounts,
        "expected_accounts": expected,
        "trip_start": metadata["start_date"] or None,
        "trip_end": metadata["end_date"] or None,
        "buffer_days": buffer_days,
        "expected_start": coverage_start.isoformat() if coverage_start else None,
        "expected_end": coverage_end.isoformat() if coverage_end else None,
        "gaps": gaps,
        "confirmation": state.get("coverage_confirmation") if state else None,
    }
