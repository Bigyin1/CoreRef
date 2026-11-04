"""Dependency graphs export to detached directed graphs without multigraph bindings."""

from dataclasses import replace

import networkx as nx
import pytest

from hwcatalog.api.models.graph import Dependency, GraphResult
from tests.helpers import graph_json, release


def test_empty_graph_and_isolated_record() -> None:
    empty = GraphResult((), ()).to_networkx()
    assert isinstance(empty, nx.DiGraph)
    assert not empty.is_multigraph()
    assert empty.number_of_nodes() == empty.number_of_edges() == 0
    assert empty.graph == {}
    record = release("isolated")
    graph = GraphResult((record,), ()).to_networkx()
    assert tuple(graph.nodes) == (record.ref,)
    assert graph.nodes[record.ref]["record"] is record


def test_export_preserves_exact_dependency_directions() -> None:
    source, left, right = (release(name) for name in ("source", "left", "right"))
    edges = (Dependency(source.ref, left.ref), Dependency(source.ref, right.ref))
    snapshot = GraphResult((source, left, right), edges)
    graph = snapshot.to_networkx()
    assert graph.number_of_nodes() == 3
    assert graph.number_of_edges() == 2
    assert not graph.has_edge(left.ref, source.ref)
    for edge in edges:
        assert graph.edges[edge.source, edge.target]["dependency"] is edge
    assert set(GraphResult(snapshot.records[::-1], edges[::-1]).to_networkx().edges) == set(
        graph.edges
    )


def test_export_is_detached_and_payloads_stay_frozen() -> None:
    source = release("source", manifest={"nested": {"ports": [1, 2]}})
    result = release("result")
    edge = Dependency(result.ref, source.ref)
    snapshot = GraphResult((source, result), (edge,))
    before = graph_json(snapshot)
    graph = snapshot.to_networkx()
    with pytest.raises(TypeError):
        graph.nodes[source.ref]["record"].payload["changed"] = True
    graph.nodes[source.ref]["record"] = replace(source, payload={"changed": True})
    graph.edges[result.ref, source.ref]["dependency"] = Dependency(source.ref, result.ref)
    graph.graph["annotation"] = "local edits"
    graph.remove_node(result.ref)
    assert graph_json(snapshot) == before
    fresh = snapshot.to_networkx()
    assert fresh.nodes[source.ref]["record"] is source
    assert fresh.edges[result.ref, source.ref]["dependency"] is edge
    assert fresh.graph == {}
