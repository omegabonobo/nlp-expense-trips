from __future__ import annotations

from typing import Literal, TypedDict, cast

ALLOCATION_TYPES = {"purchase", "refund", "fee", "personal", "ignored"}
REIMBURSABLE_ALLOCATION_TYPES = {"purchase", "refund", "fee"}

AllocationType = Literal["purchase", "refund", "fee", "personal", "ignored"]


class AllocationRecord(TypedDict):
    allocation_id: str
    type: AllocationType
    invoice_file: str | None
    category: str
    original_amount: float | None
    cad_amount: float
    percentage: float
    note: str


class AllocationTotals(TypedDict):
    allocation_total: float
    allocation_balance: float
    allocation_status: Literal["none", "balanced", "overallocated", "unallocated"]


def normalize_allocations(
    allocations: list[dict],
    available_files: set[str | None],
    target_cad: float,
) -> list[AllocationRecord]:
    if not isinstance(allocations, list):
        raise ValueError("Allocations must be submitted as a list.")
    normalized: list[AllocationRecord] = []
    for index, allocation in enumerate(allocations, start=1):
        if not isinstance(allocation, dict):
            raise ValueError(f"Allocation {index} must be an object.")
        allocation_type = str(allocation.get("type", "")).strip().lower()
        if allocation_type not in ALLOCATION_TYPES:
            raise ValueError(f"Allocation {index} has an invalid type.")
        normalized_type = cast(AllocationType, allocation_type)
        invoice_file = str(allocation.get("invoice_file") or "").strip() or None
        category = str(allocation.get("category") or "").strip()
        note = str(allocation.get("note") or "").strip()
        if invoice_file and invoice_file not in available_files:
            raise ValueError(f"Allocation {index} references an invoice that is no longer present.")
        if allocation_type in {"purchase", "refund"} and not invoice_file:
            raise ValueError(f"Allocation {index} must reference an invoice.")
        if allocation_type in {"personal", "ignored"} and not category:
            category = "Personal / non-reimbursable" if allocation_type == "personal" else "Ignored"
        if allocation_type in {"personal", "ignored"} and not note:
            raise ValueError(f"Allocation {index} needs an audit note.")
        try:
            cad_amount = round(float(allocation.get("cad_amount")), 2)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Allocation {index} needs a valid CAD amount.") from exc
        if cad_amount == 0:
            raise ValueError(f"Allocation {index} CAD amount cannot be zero.")
        if allocation_type == "refund" and cad_amount > 0:
            raise ValueError(f"Allocation {index} refund amount must be negative.")
        if allocation_type != "refund" and cad_amount < 0:
            raise ValueError(f"Allocation {index} amount must be positive.")
        original = allocation.get("original_amount")
        if original in ("", None):
            original_amount = None
        else:
            try:
                original_amount = round(float(original), 2)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Allocation {index} original amount is invalid.") from exc
        percentage = round(abs(cad_amount / target_cad) * 100, 4) if target_cad else 0.0
        normalized.append(
            {
                "allocation_id": f"A{index:03d}",
                "type": normalized_type,
                "invoice_file": invoice_file,
                "category": category,
                "original_amount": original_amount,
                "cad_amount": cad_amount,
                "percentage": percentage,
                "note": note,
            }
        )
    return normalized


def allocation_totals(allocations: list[dict], target_cad: float) -> AllocationTotals:
    total = round(sum(float(item.get("cad_amount") or 0) for item in allocations), 2)
    balance = round(target_cad - total, 2)
    if not allocations:
        status = "none"
    elif abs(balance) <= 0.01:
        status = "balanced"
        balance = 0.0
    elif target_cad and (total * target_cad < 0 or abs(total) > abs(target_cad)):
        status = "overallocated"
    else:
        status = "unallocated"
    return {"allocation_total": total, "allocation_balance": balance, "allocation_status": status}


def apply_allocations_to_groups(
    groups: list[dict], allocations_by_group: dict[str, list[dict]]
) -> None:
    for group in groups:
        allocations = allocations_by_group.get(group["group_id"], [])
        if allocations:
            apply_allocations_to_group(group, allocations)


def apply_allocations_to_group(group: dict, allocations: list[dict]) -> None:
    group.setdefault(
        "pre_allocation",
        {
            "expense_file": group.get("expense_file"),
            "match_status": group.get("match_status"),
            "match_confidence": group.get("match_confidence"),
        },
    )
    group["allocations"] = [dict(allocation) for allocation in allocations]
    group.update(allocation_totals(allocations, float(group.get("cad_amount") or 0)))
    group["expense_file"] = None
    group["match_status"] = "split"
    group["match_confidence"] = 1.0 if group["allocation_status"] == "balanced" else 0.0


def restore_group_before_allocations(group: dict) -> None:
    previous = group.get("pre_allocation", {})
    for field in ("expense_file", "match_status", "match_confidence"):
        if field in previous:
            group[field] = previous[field]
    group["allocations"] = []
    group["allocation_total"] = 0.0
    group["allocation_balance"] = group.get("cad_amount")
    group["allocation_status"] = "none"
