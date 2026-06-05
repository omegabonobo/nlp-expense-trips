from __future__ import annotations

import os
from getpass import getpass
from pathlib import Path


def load_dotenv(root: Path) -> dict[str, str]:
    env_path = root / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
        os.environ.setdefault(key.strip(), value)
    return values


def get_openai_settings(root: Path) -> tuple[str | None, str]:
    load_dotenv(root)
    return os.getenv("OPENAI_API_KEY"), os.getenv("OPENAI_MODEL", "gpt-5.2")


def configure_openai(root: Path) -> tuple[str, str]:
    existing = load_dotenv(root)
    key = getpass("OpenAI API key (input hidden): ").strip()
    if not key:
        raise ValueError("No API key entered.")
    model = existing.get("OPENAI_MODEL", os.getenv("OPENAI_MODEL", "gpt-5.2"))
    save_openai_settings(root, key, model, existing)
    return key, model


def save_openai_settings(root: Path, key: str, model: str, existing: dict[str, str] | None = None) -> None:
    env_path = root / ".env"
    existing = existing if existing is not None else load_dotenv(root)
    lines = []
    retained = {k: v for k, v in existing.items() if k not in {"OPENAI_API_KEY", "OPENAI_MODEL"}}
    for k, v in retained.items():
        lines.append(f"{k}={v}")
    lines.extend([f"OPENAI_API_KEY={key}", f"OPENAI_MODEL={model}"])
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_path.chmod(0o600)
    os.environ["OPENAI_API_KEY"] = key
    os.environ["OPENAI_MODEL"] = model


def prompt_for_openai_if_missing(root: Path) -> tuple[str, str]:
    api_key, model = get_openai_settings(root)
    if api_key:
        return api_key, model
    print("OpenAI fallback requested, but OPENAI_API_KEY is not configured.")
    print("Paste your API key below; it will be stored locally in .env with file mode 600.")
    return configure_openai(root)


def ask_openai_for_run(root: Path) -> tuple[str | None, str]:
    existing = load_dotenv(root)
    model = existing.get("OPENAI_MODEL", os.getenv("OPENAI_MODEL", "gpt-5.2"))
    answer = input(
        "Use an OpenAI API key for this run? Output quality is much better with LLM extraction. [y/N]: "
    ).strip().lower()
    if answer not in {"y", "yes"}:
        return None, model

    existing_key = existing.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if existing_key:
        prompt = "OpenAI API key (input hidden; press Enter to use the existing .env key): "
    else:
        prompt = "OpenAI API key (input hidden): "
    key = getpass(prompt).strip()
    if not key and existing_key:
        key = existing_key
    if not key:
        print("No API key entered; using heuristics only.")
        return None, model

    save_openai_settings(root, key, model, existing)
    return key, model
