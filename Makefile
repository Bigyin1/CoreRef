.PHONY: check unit integration integration-docker build

check:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .
	uv run --frozen mypy

unit:
	uv run --frozen pytest

integration:
	nix flake check

integration-docker:
	bash tests/docker/run.sh

build:
	uv build
