.PHONY: install test lint typecheck build clean

VENV := .venv/bin

install:
	uv pip install -e ".[dev]"

test:
	$(VENV)/pytest

lint:
	$(VENV)/ruff check --fix src tests || uv run ruff check --fix src tests

typecheck:
	$(VENV)/pyright || uv run pyright

check: lint typecheck test

build:
	uv build

clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache .ruff_cache
