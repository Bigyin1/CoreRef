"""Exported graphs retain domain values and bindings without sharing mutable containers."""

from dataclasses import replace

import networkx as nx
import pytest

from hwcatalog._internal.validation import normalize_relation
from hwcatalog.api.enums import RelationKind
from hwcatalog.api.models.graph import GraphResult, Relation
from tests.helpers import graph_json, release, run


def test_empty_graph_and_isolated_record() -> None:
    empty = GraphResult((), (), consistency="live").to_networkx()
    assert isinstance(empty, nx.MultiDiGraph)
    assert empty.number_of_nodes() == empty.number_of_edges() == 0
    assert empty.graph["consistency"] == "live"
    record = release("isolated")
    graph = GraphResult((record,), ()).to_networkx()
    assert tuple(graph.nodes) == (record.ref,)
    assert graph.nodes[record.ref]["record"] is record
    assert graph.graph["consistency"] == "snapshot"
    assert graph.number_of_edges() == 0


def test_multigraph_preserves_bindings_kinds_and_original_directions() -> None:
    source, result, execution = release("source"), release("result"), run("flow")
    edges = (
        Relation(execution.ref, source.ref, RelationKind.USED, "input", 0),
        Relation(execution.ref, source.ref, RelationKind.USED, "input", 1),
        Relation(execution.ref, result.ref, RelationKind.PRODUCED),
        Relation(result.ref, source.ref, RelationKind.REQUIRES, metadata={"port": [1, 2]}),
        Relation(result.ref, source.ref, RelationKind.IMPLEMENTS),
    )
    snapshot = GraphResult((source, result, execution), edges)
    graph = snapshot.to_networkx()
    assert graph.number_of_nodes() == 3
    assert graph.number_of_edges() == len(edges)
    assert graph.number_of_edges(execution.ref, source.ref) == 2
    assert graph.number_of_edges(result.ref, source.ref) == 2
    assert not graph.has_edge(result.ref, execution.ref)
    for edge in edges:
        assert graph.edges[edge.source, edge.target, edge.to_json()]["relation"] is edge
    reordered = GraphResult(snapshot.records[::-1], edges[::-1]).to_networkx()
    assert set(graph.edges) == set(reordered.edges)


def test_export_is_detached_and_keeps_nested_domain_data_frozen() -> None:
    source = release("source", manifest={"nested": {"ports": [1, 2]}})
    result = release("result")
    edge = normalize_relation(
        Relation(result.ref, source.ref, RelationKind.REQUIRES, metadata={"ports": [1, 2]})
    )
    snapshot = GraphResult((source, result), (edge,))
    binding = (result.ref, source.ref, edge.to_json())
    before = graph_json(snapshot)
    graph = snapshot.to_networkx()
    with pytest.raises(TypeError):
        graph.nodes[source.ref]["record"].payload["changed"] = True
    with pytest.raises(TypeError):
        graph.edges[binding]["relation"].metadata["changed"] = True
    graph.nodes[source.ref]["record"] = replace(source, payload={"team_id": "other"})
    graph.edges[binding]["relation"] = replace(edge, alias="other")
    graph.graph["consistency"] = "local edits"
    graph.remove_node(result.ref)
    assert graph_json(snapshot) == before
    fresh = snapshot.to_networkx()
    assert fresh.nodes[source.ref]["record"] is source
    assert fresh.graph["consistency"] == "snapshot"
    assert fresh.edges[binding]["relation"] is edge
