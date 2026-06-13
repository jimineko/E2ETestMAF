.PHONY: install browsers test lint typecheck run

install:
	python3 -m venv .venv
	.venv/bin/pip install -e '.[dev,azure-monitor]'
	npm ci

browsers:
	npx playwright install chrome

test:
	.venv/bin/pytest

lint:
	.venv/bin/ruff check .

typecheck:
	.venv/bin/mypy src

run:
	.venv/bin/python -m maf_qa

