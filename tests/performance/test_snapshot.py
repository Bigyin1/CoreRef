"""Separate first topology construction from repeated in-memory queries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from coreref.api.projects.graph import Graph
from tests.performance.support import Registry

pytestmark = [pytest.mark.performance, pytest.mark.conan_remote]
Operation = Literal["edges", "order", "metadata"]


def snapshot_operation(graph: Graph, operation: Operation) -> object:
    if operation == "edges":
        return graph.edges
    if operation == "order":
        return tuple(record.ref for record in graph.dependencies(transitive=True))
    return graph.dependencies(transitive=True)


def first_setup(
    registry: Registry,
    size: int,
) -> Callable[[], tuple[tuple[Graph], dict[str, object]]]:
    dataset = registry.dataset("shared", size)
    reader = registry.reader()
    dataset.resolve(reader)

    def setup() -> tuple[tuple[Graph], dict[str, object]]:
        return (dataset.resolve(reader),), {}

    return setup


@pytest.mark.parametrize("size", [8, 32])
@pytest.mark.parametrize("operation", ["edges", "order", "metadata"])
def test_first_snapshot_access(
    benchmark: BenchmarkFixture,
    registry: Registry,
    size: int,
    operation: Operation,
) -> None:
    def access(graph: Graph) -> object:
        return snapshot_operation(graph, operation)

    benchmark.extra_info.update(nodes=size, cache="new-graph", operation=operation)
    result = benchmark.pedantic(access, setup=first_setup(registry, size), rounds=3)
    assert result


@pytest.mark.benchmark(min_time=0.0001, max_time=0.2)
@pytest.mark.parametrize("size", [8, 32])
@pytest.mark.parametrize("operation", ["edges", "order", "metadata"])
def test_repeated_snapshot_access(
    benchmark: BenchmarkFixture,
    registry: Registry,
    size: int,
    operation: Operation,
) -> None:
    graph = registry.dataset("shared", size).resolve(registry.reader())
    expected = snapshot_operation(graph, operation)
    benchmark.extra_info.update(nodes=size, cache="same-graph", operation=operation)
    assert benchmark(snapshot_operation, graph, operation) == expected
