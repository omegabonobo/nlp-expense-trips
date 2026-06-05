.PHONY: setup test generate-melbourne

setup:
	./scripts/setup_local_env.sh

test:
	.venv/bin/python -m unittest discover -s tests -v

generate-melbourne:
	.venv/bin/python -m nlp_expenses generate trips/202606_melbourne

