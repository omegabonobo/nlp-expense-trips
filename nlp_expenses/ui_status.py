from __future__ import annotations

import os
import shutil
from pathlib import Path

from nlp_expenses.config import load_dotenv
from nlp_expenses.extraction.text import HEIC_AVAILABLE
from nlp_expenses.generator import SUPPORTED_RECEIPTS


def system_status(root: Path) -> dict:
    configured = bool(load_dotenv(root).get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"))
    return {
        "openai_configured": configured,
        "openai_env_path": str((root / ".env").resolve()),
        "tesseract_available": bool(shutil.which("tesseract")),
        "heic_available": HEIC_AVAILABLE,
        "receipt_extensions": sorted(SUPPORTED_RECEIPTS),
        "receipt_accept": ",".join(sorted(SUPPORTED_RECEIPTS)),
        "company_statement_extensions": sorted({".csv", ".xls", ".xlsx"}),
        "ivado_statement_extensions": sorted({".csv", ".pdf", ".xls", ".xlsx"}),
    }


def receipt_quality(review: dict) -> str:
    """Return the single extraction method recorded by the receipt scan."""

    quality = str(review.get("quality", "basic")).lower()
    if quality not in {"basic", "best"}:
        raise ValueError("The saved receipt extraction method is invalid. Rescan the receipts.")
    return quality
