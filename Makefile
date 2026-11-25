.PHONY: check unit integration integration-graph integration-ip integration-docker build

check:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .
	uv run --frozen mypy

unit:
	uv run --frozen pytest

integration:
	nix flake check

integration-graph:
	nix eval --impure --file tests/docker/route-graph.nix check

integration-ip:
	nix build --impure --no-link --file tests/docker/route-graph.nix counterSimulation statusSimulation counterNetlistCheck

integration-docker:
	bash tests/docker/run.sh

build:
	uv build
