.PHONY: install check unit integration conan-integration build lab

install:
	uv sync --frozen

check:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .
	uv run --frozen mypy

unit:
	uv run --frozen pytest -m 'not integration and not conan_remote'

integration:
	uv run --frozen pytest --backend=memgraph -m 'not conan and not conan_remote'

conan-integration:
	uv run --frozen pytest -m conan_remote

build:
	uv build

lab:
	docker compose -p hwcatalog-demo --profile lab up -d --wait
