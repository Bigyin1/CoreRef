"""Separate first topology construction from repeated in-memory queries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from coreref.api.graph import Graph
from tests.performance.support import Registry

pytestmark = [pytest.mark.performance, pytest.mark.conan_remote]
Operation = Literal["networkx", "order", "pruned", "metadata"]


def snapshot_operation(graph: Graph, operation: Operation) -> object:
    if operation == "networkx":
        return graph.networkx
    if operation == "order":
        return graph.ordered_refs(graph.record.ref)
    if operation == "pruned":
        return graph.ordered_refs(graph.record.ref, stop_at=(graph.requirements[-1],))
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
@pytest.mark.parametrize("operation", ["networkx", "order", "pruned", "metadata"])
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
@pytest.mark.parametrize("operation", ["networkx", "order", "pruned", "metadata"])
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
