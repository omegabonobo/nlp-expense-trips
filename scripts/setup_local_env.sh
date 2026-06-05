#!/usr/bin/env zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

CODEX_PYTHON="/Users/florent/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [[ -x "$CODEX_PYTHON" ]]; then
  PYTHON_BIN="$CODEX_PYTHON"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

echo "Using Python: $("$PYTHON_BIN" --version) at $PYTHON_BIN"

"$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[test]"

cat <<'EOF'

Local environment ready.

Run:
  .venv/bin/python -m nlp_expenses generate trips/202606_melbourne

Or:
  .venv/bin/nlp-expenses generate trips/202606_melbourne
EOF
