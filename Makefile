.PHONY: install check unit integration build performance

install:
	uv sync --frozen

check:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .
	uv run --frozen mypy

unit:
	uv run --frozen pytest -m 'not conan_remote and not performance'

integration:
	uv run --frozen pytest -m 'conan_remote and not performance'

build:
	uv build

performance:
	mkdir -p .benchmarks
	uv run --frozen pytest tests/performance -m performance --benchmark-json=.benchmarks/results.json
