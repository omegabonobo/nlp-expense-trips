PYTHON := .venv/bin/python
RUFF := .venv/bin/ruff

.PHONY: setup setup-dev test test-integration coverage lint format format-check check build package-check generate-melbourne

setup:
	./scripts/setup_local_env.sh

setup-dev:
	$(PYTHON) -m pip install -c constraints-runtime.txt -e ".[dev]"

test:
	$(PYTHON) -m pytest

test-integration:
	$(PYTHON) -m pytest -m integration

coverage:
	$(PYTHON) -m pytest --cov --cov-report=term-missing

lint:
	$(RUFF) check nlp_expenses tests scripts

format:
	$(RUFF) check --fix nlp_expenses tests scripts
	$(RUFF) format nlp_expenses tests scripts

format-check:
	$(RUFF) format --check nlp_expenses tests scripts

check: lint format-check test

build:
	$(PYTHON) -m build

package-check: build
	$(PYTHON) scripts/check_distribution.py dist

generate-melbourne:
	$(PYTHON) -m nlp_expenses generate trips/202606_melbourne
