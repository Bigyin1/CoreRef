"""Whole-tree and incremental materialization, including the same Graph repeated."""

from functools import partial

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from coreref.api.graph import Graph
from coreref.api.records import InstalledRecord
from tests.performance.support import Content, Registry, check_files, fetch, graph_setup

pytestmark = [pytest.mark.performance, pytest.mark.conan_remote]


@pytest.mark.parametrize("content", ["files", "scm", "mixed"])
@pytest.mark.parametrize("sources", [False, True], ids=["files-only", "with-sources"])
@pytest.mark.parametrize("state", ["cold", "disk", "object"])
def test_full_tree(
    benchmark: BenchmarkFixture,
    registry: Registry,
    content: Content,
    *,
    sources: bool,
    state: str,
) -> None:
    dataset = registry.dataset("shared", 8, content)
    benchmark.extra_info.update(nodes=8, content=content, sources=sources, cache=state)
    records = benchmark.pedantic(
        partial(fetch, sources=sources),
        setup=graph_setup(registry, dataset, state, sources=sources),
        rounds=3,
    )
    assert {r.ref for r in records} == {r.ref for r in dataset.records}
    check_files(records, sources=sources)
    assert all((r.source is not None) == (sources and r.has_source) for r in records)


def incremental_fetch(graph: Graph) -> tuple[InstalledRecord, ...]:
    selected = graph.ordered_refs(graph.requirements[-1])
    first = graph.get_with_sources(selected)
    remaining = tuple(
        ref for ref in graph.ordered_refs(graph.record.ref)[:-1] if ref not in selected
    )
    rest = graph.get_with_sources(remaining)
    assert graph.get_with_sources(selected) == first
    return first + rest


@pytest.mark.parametrize("state", ["cold", "disk"])
def test_incremental_tree(benchmark: BenchmarkFixture, registry: Registry, state: str) -> None:
    dataset = registry.dataset("shared", 8, "mixed")
    benchmark.extra_info.update(nodes=8, selected=3, cache=state)
    records = benchmark.pedantic(
        incremental_fetch,
        setup=graph_setup(registry, dataset, state),
        rounds=3,
    )
    assert {r.ref for r in records} == {r.ref for r in dataset.records}
    check_files(records, sources=True)
