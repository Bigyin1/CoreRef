"""Resolved project graphs expose cached topology and dependency-first traversals."""

from __future__ import annotations

import networkx as nx
import pytest

from coreref.api.catalog import Catalog
from coreref.api.graph import Dependency, Graph
from tests.helpers import graph_json, local_record, release


def test_local_root_without_dependencies(*, catalog: Catalog) -> None:
    record = release("isolated", catalog=catalog)
    snapshot = Graph(catalog, local_record(record))
    graph = snapshot.networkx
    assert isinstance(graph, nx.DiGraph)
    assert not graph.is_multigraph()
    assert graph.number_of_nodes() == 1 and graph.number_of_edges() == 0
    assert graph.graph == {}
    assert tuple(graph.nodes) == (record.ref,)
    assert graph.nodes[record.ref] == {}


def test_export_preserves_exact_dependency_directions(*, catalog: Catalog) -> None:
    source, left, right = (release(name, catalog=catalog) for name in ("source", "left", "right"))
    for record in (left, right):
        catalog.publish(local_record(record))
    snapshot = Graph(catalog, local_record(source), dependencies=(left.ref, right.ref))
    left, right = left.ref.get(), right.ref.get()
    graph = snapshot.networkx
    assert graph.number_of_nodes() == 3 and graph.number_of_edges() == 2
    assert not graph.has_edge(left.ref, source.ref)
    for edge in snapshot.edges:
        assert graph.edges[edge.source, edge.target] == {}
    reordered = Graph(catalog, local_record(source), dependencies=(right.ref, left.ref))
    assert set(reordered.networkx.edges) == set(graph.edges)


def test_working_topology_copy_does_not_change_snapshot(*, catalog: Catalog) -> None:
    source = release("source", manifest={"nested": {"ports": [1, 2]}}, catalog=catalog)
    source = catalog.publish(local_record(source))
    result = release("result", catalog=catalog)
    snapshot = Graph(catalog, local_record(result), dependencies=(source.ref,))
    before = graph_json(snapshot)
    graph = snapshot.networkx.copy()
    graph.nodes[source.ref]["annotation"] = "local edits"
    graph.edges[result.ref, source.ref]["dependency"] = Dependency(source.ref, result.ref)
    graph.graph["annotation"] = "local edits"
    graph.remove_node(result.ref)
    assert graph_json(snapshot) == before
    fresh = snapshot.networkx
    assert fresh.nodes[source.ref] == {}
    assert fresh.edges[result.ref, source.ref] == {}
    assert fresh.graph == {} and snapshot.networkx is fresh
    with pytest.raises(nx.NetworkXError, match="Frozen graph"):
        fresh.remove_node(result.ref)


def test_ordering_prunes_private_branches_and_keeps_shared_dependencies(
    *, catalog: Catalog
) -> None:
    common, cpu, gpio, private, top = (
        release(name, catalog=catalog) for name in ("common", "cpu", "gpio", "private", "top")
    )
    for record in (common, private):
        catalog.publish(local_record(record))
    cpu = catalog.publish(local_record(cpu), dependencies=(common.ref, private.ref))
    gpio = catalog.publish(local_record(gpio), dependencies=(common.ref,))
    common, cpu, gpio, private = (record.ref.get() for record in (common, cpu, gpio, private))
    snapshot = Graph(catalog, local_record(top), dependencies=(cpu.ref, gpio.ref))
    original = tuple(snapshot.networkx.edges)
    selected = snapshot.ordered_refs(top.ref, stop_at=(cpu.ref,))
    assert selected == (common.ref, cpu.ref, gpio.ref, top.ref)
    assert snapshot.ordered_refs(top.ref, stop_at=(cpu.ref,)) is selected
    assert snapshot.ordered_records(top.ref, stop_at=(cpu.ref,)) == (
        common,
        cpu,
        gpio,
        local_record(top),
    )
    assert tuple(snapshot.networkx.edges) == original
    assert snapshot.ordered_refs(top.ref) == (common.ref, gpio.ref, private.ref, cpu.ref, top.ref)
    assert snapshot.ordered_refs(cpu.ref) == (common.ref, private.ref, cpu.ref)


def test_ordering_rejects_unknown_roots_and_hidden_stops(*, catalog: Catalog) -> None:
    root, middle, leaf = (release(name, catalog=catalog) for name in ("root", "middle", "leaf"))
    leaf = catalog.publish(local_record(leaf))
    middle = catalog.publish(local_record(middle), dependencies=(leaf.ref,))
    snapshot = Graph(catalog, local_record(root), dependencies=(middle.ref,))
    absent = release("absent", catalog=catalog).ref
    with pytest.raises(ValueError, match="Unknown"):
        snapshot.ordered_refs(absent)
    for stops in ((root.ref,), (absent,)):
        with pytest.raises(ValueError, match=r"Stop references|Unknown reference"):
            snapshot.ordered_refs(root.ref, stop_at=stops)
    with pytest.raises(ValueError, match="hidden or unreachable"):
        snapshot.ordered_refs(root.ref, stop_at=(middle.ref, leaf.ref))
    with pytest.raises(ValueError, match="hidden or unreachable"):
        snapshot.ordered_refs(middle.ref, stop_at=(root.ref,))
