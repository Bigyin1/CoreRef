"""Dependency graphs export to detached directed graphs without multigraph bindings."""

from dataclasses import replace

import networkx as nx
import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.graph import Dependency, GraphResult
from tests.helpers import graph_json, release


def test_empty_graph_and_isolated_record(*, catalog: Catalog) -> None:
    empty = GraphResult((), ()).networkx
    assert isinstance(empty, nx.DiGraph)
    assert not empty.is_multigraph()
    assert empty.number_of_nodes() == empty.number_of_edges() == 0
    assert empty.graph == {}
    record = release("isolated", catalog=catalog)
    graph = GraphResult((record,), ()).networkx
    assert tuple(graph.nodes) == (record.ref,)
    assert graph.nodes[record.ref]["record"] is record


def test_export_preserves_exact_dependency_directions(*, catalog: Catalog) -> None:
    source, left, right = (release(name, catalog=catalog) for name in ("source", "left", "right"))
    edges = (Dependency(source.ref, left.ref), Dependency(source.ref, right.ref))
    snapshot = GraphResult((source, left, right), edges)
    graph = snapshot.networkx
    assert graph.number_of_nodes() == 3
    assert graph.number_of_edges() == 2
    assert not graph.has_edge(left.ref, source.ref)
    for edge in edges:
        assert graph.edges[edge.source, edge.target]["dependency"] is edge
    assert set(GraphResult(snapshot.records[::-1], edges[::-1]).networkx.edges) == set(graph.edges)


def test_export_is_detached_and_payloads_stay_frozen(*, catalog: Catalog) -> None:
    source = release("source", manifest={"nested": {"ports": [1, 2]}}, catalog=catalog)
    result = release("result", catalog=catalog)
    edge = Dependency(result.ref, source.ref)
    snapshot = GraphResult((source, result), (edge,))
    before = graph_json(snapshot)
    graph = snapshot.networkx.copy()
    with pytest.raises(TypeError):
        graph.nodes[source.ref]["record"].payload["changed"] = True
    graph.nodes[source.ref]["record"] = replace(source, payload={"changed": True})
    graph.edges[result.ref, source.ref]["dependency"] = Dependency(source.ref, result.ref)
    graph.graph["annotation"] = "local edits"
    graph.remove_node(result.ref)
    assert graph_json(snapshot) == before
    fresh = snapshot.networkx
    assert fresh.nodes[source.ref]["record"] is source
    assert fresh.edges[result.ref, source.ref]["dependency"] is edge
    assert fresh.graph == {}
    assert snapshot.networkx is fresh
    with pytest.raises(nx.NetworkXError, match="Frozen graph"):
        fresh.remove_node(result.ref)


def test_ordering_prunes_private_branches_and_keeps_shared_dependencies(
    *, catalog: Catalog
) -> None:
    common, cpu, gpio, private, top = (
        release(name, catalog=catalog) for name in ("common", "cpu", "gpio", "private", "top")
    )
    snapshot = GraphResult(
        (common, cpu, gpio, private, top),
        (
            Dependency(top.ref, cpu.ref),
            Dependency(top.ref, gpio.ref),
            Dependency(cpu.ref, common.ref),
            Dependency(cpu.ref, private.ref),
            Dependency(gpio.ref, common.ref),
        ),
    )
    original = tuple(snapshot.networkx.edges)
    selected = snapshot.ordered_refs(top.ref, stop_at=(cpu.ref,))
    assert selected == (common.ref, cpu.ref, gpio.ref, top.ref)
    assert snapshot.ordered_refs(top.ref, stop_at=(cpu.ref,)) is selected
    assert snapshot.ordered_records(top.ref, stop_at=(cpu.ref,)) == (common, cpu, gpio, top)
    assert tuple(snapshot.networkx.edges) == original
    assert snapshot.ordered_refs(top.ref) == (common.ref, gpio.ref, private.ref, cpu.ref, top.ref)
    assert snapshot.ordered_refs(cpu.ref) == (common.ref, private.ref, cpu.ref)


def test_ordering_rejects_unknown_roots_hidden_stops_and_cycles(*, catalog: Catalog) -> None:
    root, middle, leaf, isolated = (
        release(name, catalog=catalog) for name in ("root", "middle", "leaf", "isolated")
    )
    records = (root, middle, leaf, isolated)
    edges = (Dependency(root.ref, middle.ref), Dependency(middle.ref, leaf.ref))
    snapshot = GraphResult(records, edges)
    with pytest.raises(ValueError, match="Unknown"):
        GraphResult((), ()).ordered_refs(root.ref)
    for stops in ((root.ref,), (release("absent", catalog=catalog).ref,)):
        with pytest.raises(ValueError, match="Stop references"):
            snapshot.ordered_refs(root.ref, stop_at=stops)
    for hidden in ((middle.ref, leaf.ref), (isolated.ref,)):
        with pytest.raises(ValueError, match="hidden or unreachable"):
            snapshot.ordered_refs(root.ref, stop_at=hidden)
    with pytest.raises(ValueError, match="Cycle"):
        GraphResult(records, (*edges, Dependency(leaf.ref, root.ref))).ordered_refs(root.ref)
