#!/usr/bin/env zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "Python 3.11 or newer is required. Install it from python.org or with Homebrew:"
  echo "  brew install python@3.12"
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python 3.11 or newer is required. Found: $($PYTHON_BIN --version)"
  exit 1
fi

echo "Using Python: $($PYTHON_BIN --version) at $PYTHON_BIN"

if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[test]"

if ! command -v tesseract >/dev/null 2>&1; then
  echo
  echo "Warning: Tesseract OCR is not installed. Basic/offline extraction of scans will be limited."
  echo "Install it with: brew install tesseract"
fi

cat <<'EOF'

Local environment ready.

Open the local interface:
  .venv/bin/nlp-expenses ui

Or double-click "NLP Expenses.command" in Finder.
EOF
