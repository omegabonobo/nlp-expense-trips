from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

PRIVATE_FILE_MODE = 0o600


def write_text_atomic(
    path: Path,
    content: str,
    *,
    mode: int = PRIVATE_FILE_MODE,
) -> None:
    """Durably replace a text file without exposing a partially written value."""

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            mode,
        )
        try:
            handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
        except Exception:
            os.close(descriptor)
            descriptor = None
            raise
        descriptor = None  # ``handle`` owns the descriptor from this point.
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(mode)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def write_json_atomic(
    path: Path,
    value: Any,
    *,
    mode: int = PRIVATE_FILE_MODE,
) -> None:
    """Serialize JSON consistently and atomically."""

    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    write_text_atomic(path, payload, mode=mode)
