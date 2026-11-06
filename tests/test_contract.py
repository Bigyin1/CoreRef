"""The public contract contains immutable records and exact dependencies."""

from dataclasses import replace
from itertools import pairwise
from typing import cast

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import Conflict, InvalidPackage, NotFound
from hwcatalog.api.graph import Dependency
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import JSON, LocalRecord, Record, Ref
from tests.helpers import graph_json, local_record, release, resolve_published


def test_get_returns_published_records_and_reports_missing(catalog: Catalog) -> None:
    first, second = release("first", catalog=catalog), release("second", catalog=catalog)
    catalog.publish(local_record(second))
    catalog.publish(local_record(first))
    assert Ref(**first.ref.to_dict(), catalog=catalog).get() == first
    assert Ref(**second.ref.to_dict(), catalog=catalog).get() == second
    with pytest.raises(NotFound):
        Ref(**release("absent", catalog=catalog).ref.to_dict(), catalog=catalog).get()
    assert catalog.search() == (first, second)


def test_publication_is_immutable_and_conflicts_preserve_records(catalog: Catalog) -> None:
    original = release("existing", catalog=catalog)
    catalog.publish(local_record(original))
    with pytest.raises(Conflict):
        catalog.publish(local_record(replace(original, payload={"changed": True})))
    assert catalog.search() == (original,)
    assert Ref(**original.ref.to_dict(), catalog=catalog).get() == original


@pytest.mark.parametrize("count", [0, 1, 2])
def test_publish_rejects_record_collections(catalog: Catalog, count: int) -> None:
    records = tuple(release(f"record-{index}", catalog=catalog) for index in range(count))
    with pytest.raises(InvalidPackage, match="one LocalRecord"):
        catalog.publish(cast(LocalRecord, records))
    assert not catalog.search()


@pytest.mark.parametrize("problem", ["duplicate", "missing", "self", "edge-value"])
def test_invalid_dependencies_do_not_publish(catalog: Catalog, problem: str) -> None:
    source, target = release("source", catalog=catalog), release("target", catalog=catalog)
    catalog.publish(local_record(target))
    dependencies: tuple[Ref, ...] = (target.ref,)
    exception: type[Exception] = InvalidPackage
    if problem == "duplicate":
        dependencies = (target.ref, target.ref)
    elif problem == "missing":
        dependencies = (release("missing", catalog=catalog).ref,)
        exception = NotFound
    elif problem == "self":
        dependencies = (source.ref,)
    else:
        dependencies = (cast(Ref, Dependency(source.ref, target.ref)),)
    with pytest.raises(exception):
        catalog.publish(local_record(source), dependencies=dependencies)
    assert catalog.search() == (target,)


def test_dependencies_must_already_exist(catalog: Catalog) -> None:
    first, second = release("first", catalog=catalog), release("second", catalog=catalog)
    with pytest.raises(NotFound):
        catalog.publish(local_record(first), dependencies=(second.ref,))
    catalog.publish(local_record(second))
    assert catalog.publish(local_record(first), dependencies=(second.ref,)) == first
    with pytest.raises(Conflict):
        catalog.publish(local_record(second), dependencies=(first.ref,))
    assert resolve_published(catalog, first.ref).dependencies == (
        Dependency(first.ref, second.ref),
    )


def test_diamond_has_one_record_per_ref_and_exact_edges(catalog: Catalog) -> None:
    root, left, right, shared = (
        release(name, catalog=catalog) for name in ("root", "left", "right", "shared")
    )
    edges = (
        Dependency(root.ref, left.ref),
        Dependency(root.ref, right.ref),
        Dependency(left.ref, shared.ref),
        Dependency(right.ref, shared.ref),
    )
    catalog.publish(local_record(shared))
    catalog.publish(local_record(left), dependencies=(shared.ref,))
    catalog.publish(local_record(right), dependencies=(shared.ref,))
    catalog.publish(local_record(root), dependencies=(left.ref, right.ref))
    graph = resolve_published(catalog, root.ref)
    assert {record.ref for record in graph.records} == {root.ref, left.ref, right.ref, shared.ref}
    assert graph.dependencies == tuple(sorted(edges, key=lambda edge: (edge.source, edge.target)))
    assert Ref(**root.ref.to_dict(), catalog=catalog).dependencies() == (left, right)
    assert Ref(**shared.ref.to_dict(), catalog=catalog).find_dependents(
        candidates=SearchQuery(), transitive=False
    ) == (
        left,
        right,
    )
    assert Ref(**root.ref.to_dict(), catalog=catalog).dependencies(
        SearchQuery(component="left")
    ) == (left,)
    assert Ref(**shared.ref.to_dict(), catalog=catalog).find_dependents(
        candidates=SearchQuery(component="right"), transitive=False
    ) == (right,)
    assert not Ref(**root.ref.to_dict(), catalog=catalog).dependencies(
        SearchQuery(component="absent")
    )
    assert resolve_published(catalog, shared.ref).records == (local_record(shared),)


def test_resolution_is_stable_when_new_consumers_are_published(catalog: Catalog) -> None:
    root, leaf, consumer = (
        release("root", catalog=catalog),
        release("leaf", catalog=catalog),
        release("consumer", catalog=catalog),
    )
    catalog.publish(local_record(leaf))
    catalog.publish(local_record(root), dependencies=(leaf.ref,))
    graph = resolve_published(catalog, root.ref)
    before = graph_json(graph)
    catalog.publish(local_record(consumer), dependencies=(root.ref,))
    assert graph_json(resolve_published(catalog, root.ref)) == before
    assert Ref(**root.ref.to_dict(), catalog=catalog).find_dependents(
        candidates=SearchQuery(), transitive=False
    ) == (consumer,)


@pytest.mark.parametrize("length", [1, 40])
def test_resolution_returns_the_entire_graph(catalog: Catalog, length: int) -> None:
    records = tuple(release(f"part-{index:04}", catalog=catalog) for index in range(length + 1))
    edges = tuple(Dependency(source.ref, target.ref) for source, target in pairwise(records))
    catalog.publish(local_record(records[-1]))
    for source, target in reversed(tuple(pairwise(records))):
        catalog.publish(local_record(source), dependencies=(target.ref,))
    result = resolve_published(catalog, records[0].ref)
    assert result.records == (local_record(records[0]), *records[1:])
    assert set(result.dependencies) == set(edges)


def test_missing_graph_roots_are_errors(catalog: Catalog) -> None:
    ref = release("missing", catalog=catalog).ref
    with pytest.raises(NotFound):
        Ref(**ref.to_dict(), catalog=catalog).dependencies()
    with pytest.raises(NotFound):
        Ref(**ref.to_dict(), catalog=catalog).find_dependents(
            candidates=SearchQuery(), transitive=False
        )


def test_payload_changes_do_not_change_the_publication(catalog: Catalog) -> None:
    nested: list[JSON] = [1, {"nested": [2]}]
    item = Record(release("data", catalog=catalog).ref, {"items": nested})
    catalog.publish(local_record(item))
    nested.clear()
    stored = Ref(**item.ref.to_dict(), catalog=catalog).get()
    assert stored.payload == {"items": (1, {"nested": (2,)})}
