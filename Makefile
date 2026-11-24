.PHONY: install check unit integration build

install:
	uv sync --frozen

check:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .
	uv run --frozen mypy

unit:
	uv run --frozen pytest -m 'not nix'

integration:
	uv run --frozen pytest -m nix

build:
	uv build
