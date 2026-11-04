"""Public behavior shared verbatim by both backends."""

from dataclasses import replace
from itertools import pairwise

import networkx as nx
import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.enums import (
    Direction,
    LineageDirection,
    RelationKind,
)
from hwcatalog.api.errors import (
    DependencyCycle,
    IdentityConflict,
    InvalidPublication,
    NotFound,
    VersionConflict,
)
from hwcatalog.api.models.graph import Relation
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.api.types import JSON
from tests.helpers import ProjectTypeId, content, release, resources, run


def test_roundtrip_and_search(catalog: Catalog) -> None:
    a = release("one", manifest={"nested": {"array": [3, 1, 2], "bool": True, "none": None}})
    b = release("two", project_type="soc")
    catalog.publish((a, b))
    assert catalog.get(a.ref) == a
    assert catalog.get_many((b.ref, a.ref, b.ref)) == (b, a, b)
    assert content(a)["type_id"] == content(b)["type_id"]
    for query in (
        SearchQuery(name="one", version="v1"),
        SearchQuery(name="one"),
    ):
        assert catalog.search(query) == (a,)
    assert type(a).from_json(a.to_json()) == a
    with pytest.raises(NotFound):
        catalog.get(release("missing").ref)
    with pytest.raises(NotFound):
        catalog.get_many((a.ref, release("missing").ref))


def test_user_metadata_roundtrip(catalog: Catalog) -> None:
    a = Record(release("typed-a").ref, payload={"type_id": "vendor.type", "schema_version": 2})
    b = Record(release("typed-b").ref, payload={"type_id": "vendor.type", "schema_version": False})
    r = Record(run("typed-run").ref, payload={"schema_version": "anything"})
    catalog.publish((a, b, r))
    assert catalog.get_many((a.ref, b.ref, r.ref)) == (a, b, r)


def test_defensive_data(catalog: Catalog) -> None:
    array: list[JSON] = [1, 2]
    original: dict[str, JSON] = {"nested": array}
    record = replace(release("immutable"), payload=original)
    array.append(3)
    catalog.publish((record,))
    array.append(4)
    original["other"] = "after publication"
    result = catalog.get(record.ref)
    assert isinstance(result, Record)
    assert content(result)["nested"] == (1, 2, 3)
    assert "other" not in content(result)
    with pytest.raises(TypeError):
        content(result)["nested"] = ()  # type: ignore[index]
    assert catalog.get(record.ref) == result
    assert content(record)["nested"] == [1, 2, 3, 4]


def test_existing_release_is_not_republished(catalog: Catalog) -> None:
    record = release("once")
    catalog.publish((record,))
    with pytest.raises(IdentityConflict):
        catalog.publish((record,))
    with pytest.raises(IdentityConflict):
        catalog.publish((replace(record, payload={"changed": True}),))
    assert catalog.get(record.ref) == record


def test_invalid_batch_does_not_write(catalog: Catalog) -> None:
    a, b = release("a"), release("b")
    edges = (Relation(a.ref, b.ref, RelationKind.REQUIRES),)
    with pytest.raises(InvalidPublication):
        catalog.publish((a,), relations=edges)
    with pytest.raises(NotFound):
        catalog.get(a.ref)
    catalog.publish((a, b), relations=edges)
    assert catalog.get_many((a.ref, b.ref)) == (a, b)


def test_reordered_metadata_does_not_create_a_distinct_binding(catalog: Catalog) -> None:
    source, target = release("source"), release("target")
    edge = Relation(
        source.ref, target.ref, RelationKind.REQUIRES, metadata={"z": 2, "a": {"y": 1, "x": 0}}
    )
    reordered = replace(edge, metadata={"a": {"x": 0, "y": 1}, "z": 2})
    with pytest.raises(InvalidPublication, match="duplicate relation"):
        catalog.publish((source, target), relations=(edge, reordered))
    assert not catalog.search()


@pytest.mark.parametrize(
    "scenario",
    [
        "duplicate",
        "alias",
        "mixed_ordinal",
        "self",
        "late_source",
        "late_producer",
        "two_producers",
        "duplicate_id",
    ],
)
def test_invalid_publications(catalog: Catalog, scenario: str) -> None:
    old, a, b, r, r2 = release("old"), release("a"), release("b"), run("r"), run("r2")
    catalog.publish((old,))
    edge = Relation(a.ref, b.ref, RelationKind.REQUIRES, "role")
    edges = {
        "duplicate": (edge, edge),
        "alias": (edge, replace(edge, metadata={"other": True})),
        "mixed_ordinal": (edge, replace(edge, ordinal=1)),
        "self": (Relation(a.ref, a.ref, RelationKind.REQUIRES),),
        "late_source": (Relation(old.ref, a.ref, RelationKind.REQUIRES),),
        "late_producer": (Relation(r.ref, old.ref, RelationKind.PRODUCED),),
        "two_producers": (
            Relation(r.ref, a.ref, RelationKind.PRODUCED),
            Relation(r2.ref, a.ref, RelationKind.PRODUCED),
        ),
        "duplicate_id": (),
    }[scenario]
    records = (a, a) if scenario == "duplicate_id" else (a, b, r, r2)
    with pytest.raises(InvalidPublication):
        catalog.publish(records, relations=edges)
    assert catalog.search() == (old,)


@pytest.mark.parametrize("causal", [False, True])
def test_cycles(catalog: Catalog, *, causal: bool) -> None:
    a, b, r = release("a"), release("b"), run("r")
    edges = [Relation(a.ref, b.ref, RelationKind.REQUIRES)]
    if causal:
        edges.extend(
            (
                Relation(r.ref, a.ref, RelationKind.USED),
                Relation(r.ref, b.ref, RelationKind.PRODUCED),
            )
        )
    else:
        edges.append(Relation(b.ref, a.ref, RelationKind.REQUIRES))
    with pytest.raises(DependencyCycle):
        catalog.publish((a, b, r), relations=tuple(edges))
    assert not catalog.search()


def test_binding_diamond(catalog: Catalog) -> None:
    root, left, right, shared = [release(n) for n in ("root", "left", "right", "shared")]
    edges = (
        Relation(root.ref, left.ref, RelationKind.REQUIRES, "left"),
        Relation(root.ref, right.ref, RelationKind.REQUIRES, "right"),
        Relation(left.ref, shared.ref, RelationKind.REQUIRES, "lib"),
        Relation(right.ref, shared.ref, RelationKind.REQUIRES, "lib"),
        Relation(root.ref, left.ref, RelationKind.REQUIRES, "extra"),
    )
    catalog.publish((root, left, right, shared), relations=edges)
    graph = catalog.graph.dependency_closure(root.ref)
    assert len(graph.records) == 4
    assert {e.to_json() for e in graph.relations} == {e.to_json() for e in edges}
    exported = graph.to_networkx()
    assert exported.number_of_nodes() == 4
    assert exported.number_of_edges() == 5
    assert exported.number_of_edges(root.ref, left.ref) == 2
    assert nx.shortest_path_length(exported, root.ref, shared.ref) == 2
    assert len(catalog.graph.dependencies(root.ref)) == 3
    assert len(catalog.graph.consumers(shared.ref)) == 2
    assert catalog.graph.dependency_closure(shared.ref).records == (shared,)


@pytest.mark.parametrize(("length", "bindings"), [(40, 1), (1001, 5)])
def test_dependency_closure_returns_the_entire_graph(
    catalog: Catalog, length: int, bindings: int
) -> None:
    records = tuple(release(f"part-{i:04}") for i in range(length + 1))
    edges = tuple(
        Relation(source.ref, target.ref, RelationKind.REQUIRES, "input", ordinal)
        for source, target in pairwise(records)
        for ordinal in range(bindings)
    )
    catalog.publish(records, relations=edges)
    result = catalog.graph.dependency_closure(records[0].ref)
    assert result.records == records
    assert len(result.relations) == len(edges)
    assert {edge.to_json() for edge in result.relations} == {edge.to_json() for edge in edges}


def test_dependency_closure_reports_conflicts_and_can_return_exact_graph(catalog: Catalog) -> None:
    root, v1, v2, leaf = release("root"), release("lib", "1"), release("lib", "2"), release("leaf")
    edges = (
        Relation(root.ref, v1.ref, RelationKind.REQUIRES),
        Relation(root.ref, v2.ref, RelationKind.REQUIRES),
        Relation(v2.ref, leaf.ref, RelationKind.REQUIRES),
    )
    catalog.publish((root, v1, v2, leaf), relations=edges)
    with pytest.raises(VersionConflict, match="lib"):
        catalog.graph.dependency_closure(root.ref, resolve_versions=True)
    graph = catalog.graph.dependency_closure(root.ref, resolve_versions=False)
    assert {record.ref for record in graph.records} == {root.ref, v1.ref, v2.ref, leaf.ref}
    assert {edge.to_json() for edge in graph.relations} == {edge.to_json() for edge in edges}


def test_late_results_and_lineage(catalog: Catalog) -> None:
    rtl, lib, dft, be1, be2 = [release(n) for n in ("rtl", "lib", "dft", "be1", "be2")]
    edge = Relation(rtl.ref, lib.ref, RelationKind.REQUIRES)
    catalog.publish((rtl, lib), relations=(edge,))
    assert not catalog.graph.implementations(rtl.ref)
    dft_run = run("dft-run")
    catalog.publish(
        (dft, dft_run),
        relations=(
            Relation(dft_run.ref, rtl.ref, RelationKind.USED),
            Relation(dft_run.ref, dft.ref, RelationKind.PRODUCED),
        ),
    )
    assert not catalog.graph.implementations(rtl.ref)  # No inferred IMPLEMENTS.
    for index, be in enumerate((be1, be2)):
        be = replace(
            be, payload={**content(be), "team_id": "be", "type_id": ProjectTypeId.HARDMACRO}
        )
        be_run = run(f"be-{index}")
        edges = (
            Relation(be.ref, rtl.ref, RelationKind.IMPLEMENTS),
            Relation(be_run.ref, dft.ref, RelationKind.USED),
            Relation(be_run.ref, be.ref, RelationKind.PRODUCED),
        )
        catalog.publish((be, be_run), relations=edges)
        for item in edges:
            assert catalog.graph.neighbors(item.source, item.kind)
            assert catalog.graph.neighbors(item.target, item.kind, Direction.INCOMING)
    implementations = catalog.graph.implementations(rtl.ref, SearchQuery())
    assert len(implementations) == 2
    upstream = catalog.graph.lineage(be1.ref, LineageDirection.UPSTREAM)
    assert {r.ref for r in upstream.records} == {
        be1.ref,
        run("be-0").ref,
        dft.ref,
        dft_run.ref,
        rtl.ref,
    }
    assert len(upstream.relations) == 4
    exported = upstream.to_networkx()
    assert exported.has_edge(dft_run.ref, rtl.ref)
    assert exported.has_edge(dft_run.ref, dft.ref)
    assert not exported.has_edge(dft.ref, dft_run.ref)
    downstream = catalog.graph.lineage(rtl.ref, LineageDirection.DOWNSTREAM)
    assert len(downstream.records) == 7
    assert catalog.get(rtl.ref) == rtl
    assert tuple(n.relation for n in catalog.graph.dependencies(rtl.ref)) == (edge,)


def test_runs_without_outputs(catalog: Catalog) -> None:
    record = run("check")
    catalog.publish((record,))
    assert catalog.get(record.ref) == record
    assert not catalog.graph.neighbors(record.ref, RelationKind.PRODUCED)
    assert catalog.search(SearchQuery(name=record.ref.name)) == (record,)
    assert type(record).from_json(record.to_json()) == record


def test_lineage_uses_relations_without_distinct_node_types(catalog: Catalog) -> None:
    root, source, producer, consumer, output = (
        Record(Ref(name, "1", "release", "demo"))
        for name in ("root", "source", "producer", "consumer", "output")
    )
    edges = (
        Relation(root.ref, source.ref, RelationKind.USED),
        Relation(producer.ref, root.ref, RelationKind.PRODUCED),
        Relation(consumer.ref, root.ref, RelationKind.USED),
        Relation(root.ref, output.ref, RelationKind.PRODUCED),
    )
    catalog.publish((root, source, producer, consumer, output), relations=edges)
    upstream = catalog.graph.lineage(root.ref, LineageDirection.UPSTREAM)
    downstream = catalog.graph.lineage(root.ref, LineageDirection.DOWNSTREAM)
    assert {record.ref for record in upstream.records} == {root.ref, source.ref, producer.ref}
    assert {record.ref for record in downstream.records} == {root.ref, consumer.ref, output.ref}
    assert catalog.graph.dependency_closure(root.ref).records == (root,)


def test_multiple_outputs_and_source_without_producer(catalog: Catalog) -> None:
    r, a, b = run("multi"), release("a"), release("b")
    catalog.publish(
        (r, a, b),
        relations=(
            Relation(r.ref, a.ref, RelationKind.PRODUCED),
            Relation(r.ref, b.ref, RelationKind.PRODUCED),
        ),
    )
    assert len(catalog.graph.neighbors(r.ref, RelationKind.PRODUCED)) == 2
    source = release("source")
    catalog.publish((source,))
    assert len(catalog.graph.lineage(source.ref, LineageDirection.UPSTREAM).records) == 1


def test_same_commit_distinct_dependencies(catalog: Catalog) -> None:
    rtl1, rtl2 = release("rtl", "1"), release("rtl", "2")
    lib1, lib2 = release("lib", "1"), release("lib", "2")
    assert resources(rtl1) == resources(rtl2)
    catalog.publish(
        (rtl1, rtl2, lib1, lib2),
        relations=(
            Relation(rtl1.ref, lib1.ref, RelationKind.REQUIRES),
            Relation(rtl2.ref, lib2.ref, RelationKind.REQUIRES),
        ),
    )
    assert catalog.graph.dependencies(rtl1.ref)[0].record == lib1
    assert catalog.graph.dependencies(rtl2.ref)[0].record == lib2


def test_search_returns_all_matches_and_reads_new_publications(catalog: Catalog) -> None:
    records = tuple(release(f"part-{i:03}") for i in range(125))
    catalog.publish(tuple(reversed(records)))
    first = catalog.search()
    assert first == records
    assert catalog.search(SearchQuery(name="absent")) == ()

    latest = release("part-new")
    catalog.publish((latest,))
    assert catalog.search() == (*records, latest)
    assert first == records


def test_neighbors_return_all_bindings_and_read_new_publications(catalog: Catalog) -> None:
    rtl = release("rtl")
    implementations = tuple(release(f"be-{i:03}", team="be") for i in range(125))
    edges = tuple(Relation(be.ref, rtl.ref, RelationKind.IMPLEMENTS) for be in implementations)
    catalog.publish((rtl, *reversed(implementations)), relations=edges[::-1])
    first = catalog.graph.implementations(rtl.ref, SearchQuery())
    assert tuple(n.record for n in first) == implementations
    assert tuple(n.relation for n in first) == edges
    assert catalog.graph.implementations(rtl.ref, SearchQuery(name="absent")) == ()

    latest = release("be-new", team="be")
    catalog.publish((latest,), relations=(Relation(latest.ref, rtl.ref, RelationKind.IMPLEMENTS),))
    assert tuple(n.record for n in catalog.graph.implementations(rtl.ref)) == (
        *implementations,
        latest,
    )
    assert tuple(n.record for n in first) == implementations


def test_empty_alias_bindings_and_implements_cycle(catalog: Catalog) -> None:
    a, b = release("a"), release("b")
    edges = (
        Relation(a.ref, b.ref, RelationKind.REQUIRES, metadata={"port": 1}),
        Relation(a.ref, b.ref, RelationKind.REQUIRES, metadata={"port": 2}),
        Relation(a.ref, b.ref, RelationKind.IMPLEMENTS),
        Relation(b.ref, a.ref, RelationKind.IMPLEMENTS),
    )
    catalog.publish((a, b), relations=edges)
    assert len(catalog.graph.dependencies(a.ref)) == 2
    assert len(catalog.graph.dependency_closure(a.ref).relations) == 2
    assert catalog.graph.implementations(a.ref)[0].record == b


def test_missing_neighbors_and_manifest_is_data(catalog: Catalog) -> None:
    a = release("data", manifest={"class": "os.system", "package": "arbitrary", "code": "exit()"})
    catalog.publish((a,))
    assert catalog.get(a.ref) == a
    with pytest.raises(NotFound):
        catalog.graph.dependencies(release("absent").ref)


def test_relation_and_run_json_is_defensive(catalog: Catalog) -> None:
    array: list[JSON] = [1, 2]
    data: dict[str, JSON] = {"ordered": array}
    a, r = release("a"), replace(run("r"), payload=data)
    relation = Relation(r.ref, a.ref, RelationKind.USED, metadata=data)
    catalog.publish((a, r), relations=(relation,))
    array.append(3)
    data["other"] = "later"
    stored = catalog.graph.neighbors(r.ref, RelationKind.USED)[0].relation
    assert stored.metadata["ordered"] == (1, 2)
    assert "other" not in stored.metadata
    stored_run = catalog.get(r.ref)
    assert isinstance(stored_run, Record)
    assert content(stored_run)["ordered"] == (1, 2)
    assert "other" not in content(stored_run)
    with pytest.raises(TypeError):
        stored.metadata["changed"] = True  # type: ignore[index]


def test_multiple_bindings_of_single_producer(catalog: Catalog) -> None:
    a, r = release("dual-view"), run("producer")
    edges = (
        Relation(r.ref, a.ref, RelationKind.PRODUCED, "netlist"),
        Relation(r.ref, a.ref, RelationKind.PRODUCED, "timing"),
    )
    catalog.publish((a, r), relations=edges)
    assert len(catalog.graph.neighbors(r.ref, RelationKind.PRODUCED)) == 2
    lineage = catalog.graph.lineage(a.ref, LineageDirection.UPSTREAM)
    assert len(lineage.records) == 2
    assert len(lineage.relations) == 2
