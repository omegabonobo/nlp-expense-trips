#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ -n "${NLP_EXPENSES_ROOT:-}" ]]; then
  DATA_ROOT="$NLP_EXPENSES_ROOT"
elif [[ -f "$ROOT_DIR/.env" || -d "$ROOT_DIR/trips" ]]; then
  # Preserve the original layout for existing checkouts.
  DATA_ROOT="$ROOT_DIR"
else
  # Keep private trip data outside fresh clones and downloaded source archives.
  DATA_ROOT="$HOME/Documents/NLP Expenses Data"
fi

mkdir -p "$DATA_ROOT"

if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'import flask, nlp_expenses' >/dev/null 2>&1; then
  echo "Preparing NLP Expenses for first use..."
  ./scripts/setup_local_env.sh
fi

if ! command -v tesseract >/dev/null 2>&1; then
  echo "Warning: Tesseract OCR is missing. Install it with: brew install tesseract"
fi

echo "Private data folder: $DATA_ROOT"
exec .venv/bin/python -m nlp_expenses --root "$DATA_ROOT" ui
