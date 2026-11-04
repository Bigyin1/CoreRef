.PHONY: install check unit integration build

install:
	uv sync --frozen

check:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .
	uv run --frozen mypy

unit:
	uv run --frozen pytest -m 'not conan_remote'

integration:
	uv run --frozen pytest -m conan_remote

build:
	uv build
