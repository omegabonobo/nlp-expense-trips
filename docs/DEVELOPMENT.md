# Development and packaging

This repository is a local-first Python application. Receipt images, card
statements, generated workbooks, review state, and API keys are runtime data;
they are not source fixtures and must not be committed.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the stable public facades, internal
module boundaries, processing flow, and characterization-test contracts.

## Prerequisites

- macOS for the Finder launcher and `open` integrations;
- Python 3.11 or newer;
- Tesseract (`brew install tesseract`) for useful Basic/offline OCR;
- an individual OpenAI API key only when Best quality is used.

The Python modules and tests are platform-neutral where practical, but the
double-click launcher is intentionally Mac-specific.

## Set up a checkout

For normal app use:

```bash
./scripts/setup_local_env.sh
```

For development, add the formatter, linter, coverage, and build tooling:

```bash
make setup-dev
make check
```

The main commands are:

| Command | Purpose |
| --- | --- |
| `make test` | Run the reproducible pytest suite with warnings treated as errors. |
| `make test-integration` | Run opt-in checks that depend on local trip data. |
| `make coverage` | Run branch coverage and show missed lines. |
| `make lint` | Run the configured Ruff correctness/style checks. |
| `make format` | Apply safe Ruff fixes and format Python files. |
| `make check` | Run lint, formatting verification, and tests. |
| `make build` | Create an sdist and wheel under `dist/`. |
| `make package-check` | Build and verify all runtime/contributor archive assets. |

GitHub CI runs the same checks on the minimum supported Python 3.11, the local
development series 3.12, and the current stable series 3.14. The 3.14 job also
builds and inspects the wheel from outside the checkout.

## Runtime data root

By default, the CLI treats its current working directory as the data root. The
root contains `trips/`, local accounting defaults, and `.env`. An installed
command can use a separate location without copying the source tree:

```bash
nlp-expenses --root ~/Documents/ivado-expenses ui
NLP_EXPENSES_ROOT=~/Documents/ivado-expenses nlp-expenses ui
```

Keep `--root` before the subcommand. Do not point multiple running app
processes at the same data root; in-process job locks do not coordinate across
separate processes.

## Build and smoke-test a distribution

Build from a clean checkout after `make check`:

```bash
make build
python3 -m venv /tmp/nlp-expenses-wheel-test
/tmp/nlp-expenses-wheel-test/bin/python -m pip install dist/nlp_expenses-*.whl
cd /tmp
/tmp/nlp-expenses-wheel-test/bin/nlp-expenses --version
/tmp/nlp-expenses-wheel-test/bin/python -c \
  'from nlp_expenses.trip_manifest import CONTRACT_SCHEMA; assert CONTRACT_SCHEMA.is_file()'
```

The last check is important: contract schemas, the HTML template, JavaScript,
CSS, and the standard-statement CSV are runtime package data.

## Change discipline

- Preserve manifest compatibility unless the schema version is deliberately
  bumped and the downstream `arvine-accounting-expenses` copy is updated.
- Add regression coverage for receipt parsing, money/date normalization, and
  lifecycle changes; these areas can produce plausible but incorrect outputs.
- Keep machine-specific trip checks marked `integration`; the default suite
  must be reproducible from a fresh clone with no private trip data.
- Keep filesystem writes inside a validated trip or data root and use the
  shared atomic storage helpers for JSON/text state.
- Never add real receipts, card exports, `.env`, workbooks, or generated trip
  packages as test data.

This repository currently has no declared public license. Internal sharing is
unaffected, but choose and add a license before distributing it outside the
organization.
