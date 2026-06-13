.PHONY: install browsers test lint typecheck run build upgrade

install:
	uv python install
	uv sync --all-extras --group dev
	npm ci

browsers:
	npx playwright install chrome

test:
	uv run pytest

lint:
	uv run ruff check .

typecheck:
	uv run mypy src

run:
	uv run maf-qa

build:
	uv build

upgrade:
	uv lock --upgrade
