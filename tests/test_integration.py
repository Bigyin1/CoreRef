"""Basic real-Memgraph behavior for the prototype."""

import pytest
from neo4j import GraphDatabase

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.enums import LineageDirection, RelationKind
from hwcatalog.api.errors import BackendUnavailable, VersionConflict
from hwcatalog.api.models.graph import Relation
from hwcatalog.backends.memgraph import MemgraphBackend
from tests.conftest import Compose, Factory
from tests.helpers import (
    graph_json,
    greedy_resolution_graph,
    release,
    resolution_graph,
    resolve_in_process,
    run,
)

pytestmark = pytest.mark.integration


def test_new_client_reads_published_records(backend_factory: Factory) -> None:
    record = release("reconnect")
    with Catalog(backend_factory()) as first:
        first.publish((record,))
    with Catalog(backend_factory()) as second:
        assert second.get(record.ref) == record


@pytest.mark.parametrize("greedy", [False, True])
def test_resolution_survives_reconnection(backend_factory: Factory, greedy: bool) -> None:
    root, records, relations = greedy_resolution_graph() if greedy else resolution_graph()
    with Catalog(backend_factory()) as first:
        first.publish(records, relations=relations)
        expected = graph_json(first.graph.dependency_closure(root.ref, resolve_versions=True))
    with Catalog(backend_factory()) as second:
        assert (
            graph_json(second.graph.dependency_closure(root.ref, resolve_versions=True)) == expected
        )


def test_flow_provenance_and_implementations(catalog: Catalog) -> None:
    rtl, macro, execution = release("rtl"), release("hardmacro"), run("synthesis")
    catalog.publish((rtl,))
    relations = (
        Relation(execution.ref, rtl.ref, RelationKind.USED, "design"),
        Relation(execution.ref, macro.ref, RelationKind.PRODUCED, "macro"),
        Relation(macro.ref, rtl.ref, RelationKind.IMPLEMENTS, "design"),
    )
    catalog.publish((macro, execution), relations=relations)

    assert {neighbor.record.ref for neighbor in catalog.graph.implementations(rtl.ref)} == {
        macro.ref
    }
    upstream = catalog.graph.lineage(macro.ref, LineageDirection.UPSTREAM)
    downstream = catalog.graph.lineage(rtl.ref, LineageDirection.DOWNSTREAM)
    assert {record.ref for record in upstream.records} == {rtl.ref, macro.ref, execution.ref}
    assert downstream == upstream
    assert {edge.to_json() for edge in upstream.relations} == {
        edge.to_json() for edge in relations[:2]
    }
    graph = upstream.to_networkx()
    assert graph.number_of_nodes() == 3
    assert graph.number_of_edges() == 2


def test_lab_names_and_generic_storage(catalog: Catalog, compose: Compose) -> None:
    source, execution = release("source"), run("lint-run")
    catalog.publish((source, execution))
    with GraphDatabase.driver(compose.uri) as driver, driver.session() as session:
        rows = session.run("MATCH (n:HCRecord) RETURN properties(n) AS p").data()
    assert {row["p"]["name"] for row in rows} == {"source", "lint-run"}
    assert all(
        set(row["p"]) == {"name", "version", "kind", "namespace", "key", "payload"} for row in rows
    )
    assert catalog.get(execution.ref) == execution


@pytest.mark.restart
@pytest.mark.parametrize("greedy", [False, True])
def test_saved_graph_survives_restart(catalog: Catalog, compose: Compose, greedy: bool) -> None:
    root, records, relations = greedy_resolution_graph() if greedy else resolution_graph()
    catalog.publish(records, relations=relations)
    resolved = graph_json(catalog.graph.dependency_closure(root.ref, resolve_versions=True))
    conflict, v1, v2 = release("conflict"), release("unresolved", "1"), release("unresolved", "2")
    catalog.publish(
        (conflict, v1, v2),
        relations=(
            Relation(conflict.ref, v1.ref, RelationKind.REQUIRES),
            Relation(conflict.ref, v2.ref, RelationKind.REQUIRES),
        ),
    )
    with pytest.raises(VersionConflict) as failure:
        catalog.graph.dependency_closure(conflict.ref, resolve_versions=True)
    expected_error = f"VersionConflict: {failure.value}"
    assert resolve_in_process(root.ref, uri=compose.uri, hash_seed="7") == resolved
    records = catalog.search()
    catalog.close()
    compose.run("restart", "memgraph")
    compose.ready()
    with Catalog(MemgraphBackend(compose.uri)) as restored:
        assert restored.search() == records
        assert (
            graph_json(restored.graph.dependency_closure(root.ref, resolve_versions=True))
            == resolved
        )
        assert resolve_in_process(root.ref, uri=compose.uri, hash_seed="91") == resolved
        assert resolve_in_process(conflict.ref, uri=compose.uri, hash_seed="91") == expected_error


def test_connection_failure_is_not_missing(catalog: Catalog, compose: Compose) -> None:
    record = release("available")
    catalog.publish((record,))
    compose.run("stop", "memgraph")
    try:
        with pytest.raises(BackendUnavailable):
            catalog.get(record.ref)
        with pytest.raises(BackendUnavailable):
            MemgraphBackend(compose.uri)
    finally:
        compose.run("start", "memgraph")
        compose.ready()
    assert catalog.get(record.ref) == record
