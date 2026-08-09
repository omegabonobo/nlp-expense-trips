from __future__ import annotations

import re


SYSTEM_TAX_LINES = {"gst_hst": "GST/HST", "qst": "QST"}


def tax_line_type(description: object) -> str | None:
    """Map Canadian and French tax labels to their canonical structured field."""

    normalized = re.sub(r"[^A-Z]+", " ", str(description or "").upper()).strip()
    if normalized in {"GST", "HST", "GST HST", "TPS", "TVH", "TPS TVH"}:
        return "gst_hst"
    if normalized in {"QST", "TVQ"}:
        return "qst"
    return None
