.PHONY: check unit integration build

check:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .
	uv run --frozen mypy

unit:
	uv run --frozen pytest

integration:
	nix flake check

build:
	uv build
