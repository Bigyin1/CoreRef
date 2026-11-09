"""Resolve differently shaped graphs with cold recipes, warm recipes, and locks."""

from __future__ import annotations

from collections.abc import Callable
from operator import methodcaller
from pathlib import Path

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from coreref.api.graph import Graph
from tests.performance.support import Dataset, Registry, Shape

pytestmark = [pytest.mark.performance, pytest.mark.conan_remote]
_CALL = methodcaller("__call__")


def resolution_setup(
    registry: Registry,
    dataset: Dataset,
    state: str,
    lockfile: Path | None,
) -> Callable[[], tuple[tuple[Callable[[], Graph]], dict[str, object]]]:
    reader = registry.reader()
    if state == "disk":
        dataset.resolve(reader, lockfile=lockfile)

    def setup() -> tuple[tuple[Callable[[], Graph]], dict[str, object]]:
        catalog = registry.reader() if state == "cold" else reader
        return (lambda: dataset.resolve(catalog, lockfile=lockfile),), {}

    return setup


@pytest.mark.parametrize("shape", ["chain", "wide", "shared"])
@pytest.mark.parametrize("size", [8, 32])
@pytest.mark.parametrize("state", ["cold", "disk"])
def test_resolve(
    benchmark: BenchmarkFixture,
    registry: Registry,
    shape: Shape,
    size: int,
    *,
    state: str,
) -> None:
    dataset = registry.dataset(shape, size)
    benchmark.extra_info.update(nodes=size, shape=shape, cache=state)
    graph = benchmark.pedantic(
        _CALL,
        setup=resolution_setup(registry, dataset, state, None),
        rounds=3,
    )
    dataset.check(graph)


@pytest.mark.parametrize("state", ["cold", "disk"])
def test_locked_resolve(
    benchmark: BenchmarkFixture,
    registry: Registry,
    tmp_path: Path,
    state: str,
) -> None:
    dataset = registry.dataset("shared", 32)
    lock = dataset.resolve(registry.reader()).create_lockfile(tmp_path / "project.lock")
    benchmark.extra_info.update(nodes=32, cache=state, lockfile=True)
    graph = benchmark.pedantic(
        _CALL,
        setup=resolution_setup(registry, dataset, state, lock),
        rounds=3,
    )
    dataset.check(graph)
