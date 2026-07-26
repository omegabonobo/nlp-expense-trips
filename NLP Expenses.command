#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'import flask, nlp_expenses' >/dev/null 2>&1; then
  echo "Preparing NLP Expenses for first use..."
  ./scripts/setup_local_env.sh
fi

if ! command -v tesseract >/dev/null 2>&1; then
  echo "Warning: Tesseract OCR is missing. Install it with: brew install tesseract"
fi

exec .venv/bin/python -m nlp_expenses ui
