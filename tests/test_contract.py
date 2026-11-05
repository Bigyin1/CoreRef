"""The public contract contains immutable records and exact dependencies."""

from dataclasses import replace
from itertools import pairwise
from typing import cast

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import Conflict, InvalidPackage, NotFound
from hwcatalog.api.models.graph import Dependency
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.api.types import JSON
from tests.helpers import graph_json, release, resolve_published


def test_get_returns_published_records_and_reports_missing(catalog: Catalog) -> None:
    first, second = release("first"), release("second")
    catalog.publish(second)
    catalog.publish(first)
    assert catalog.get(first.ref) == first
    assert catalog.get(second.ref) == second
    with pytest.raises(NotFound):
        catalog.get(release("absent").ref)
    assert catalog.search() == (first, second)


def test_publication_is_immutable_and_conflicts_preserve_records(catalog: Catalog) -> None:
    original = release("existing")
    catalog.publish(original)
    with pytest.raises(Conflict):
        catalog.publish(replace(original, payload={"changed": True}))
    assert catalog.search() == (original,)
    assert catalog.get(original.ref) == original


@pytest.mark.parametrize("count", [0, 1, 2])
def test_publish_rejects_record_collections(catalog: Catalog, count: int) -> None:
    records = tuple(release(f"record-{index}") for index in range(count))
    with pytest.raises(InvalidPackage, match="one Record"):
        catalog.publish(cast(Record, records))
    assert not catalog.search()


@pytest.mark.parametrize("problem", ["duplicate", "missing", "self", "edge-value"])
def test_invalid_dependencies_do_not_publish(catalog: Catalog, problem: str) -> None:
    source, target = release("source"), release("target")
    catalog.publish(target)
    dependencies: tuple[Ref, ...] = (target.ref,)
    exception: type[Exception] = InvalidPackage
    if problem == "duplicate":
        dependencies = (target.ref, target.ref)
    elif problem == "missing":
        dependencies = (release("missing").ref,)
        exception = NotFound
    elif problem == "self":
        dependencies = (source.ref,)
    else:
        dependencies = (cast(Ref, Dependency(source.ref, target.ref)),)
    with pytest.raises(exception):
        catalog.publish(source, dependencies=dependencies)
    assert catalog.search() == (target,)


def test_dependencies_must_already_exist(catalog: Catalog) -> None:
    first, second = release("first"), release("second")
    with pytest.raises(NotFound):
        catalog.publish(first, dependencies=(second.ref,))
    catalog.publish(second)
    assert catalog.publish(first, dependencies=(second.ref,)) == first
    with pytest.raises(Conflict):
        catalog.publish(second, dependencies=(first.ref,))
    assert resolve_published(catalog, first.ref).dependencies == (
        Dependency(first.ref, second.ref),
    )


def test_diamond_has_one_record_per_ref_and_exact_edges(catalog: Catalog) -> None:
    root, left, right, shared = (release(name) for name in ("root", "left", "right", "shared"))
    edges = (
        Dependency(root.ref, left.ref),
        Dependency(root.ref, right.ref),
        Dependency(left.ref, shared.ref),
        Dependency(right.ref, shared.ref),
    )
    catalog.publish(shared)
    catalog.publish(left, dependencies=(shared.ref,))
    catalog.publish(right, dependencies=(shared.ref,))
    catalog.publish(root, dependencies=(left.ref, right.ref))
    graph = resolve_published(catalog, root.ref)
    assert {record.ref for record in graph.records} == {root.ref, left.ref, right.ref, shared.ref}
    assert graph.dependencies == tuple(sorted(edges, key=lambda edge: (edge.source, edge.target)))
    assert catalog.dependencies(root.ref) == (left, right)
    assert catalog.find_dependents(shared.ref, candidates=SearchQuery(), transitive=False) == (
        left,
        right,
    )
    assert catalog.dependencies(root.ref, SearchQuery(name="left")) == (left,)
    assert catalog.find_dependents(
        shared.ref, candidates=SearchQuery(name="right"), transitive=False
    ) == (right,)
    assert not catalog.dependencies(root.ref, SearchQuery(name="absent"))
    assert resolve_published(catalog, shared.ref).records == (shared,)


def test_resolution_is_stable_when_new_consumers_are_published(catalog: Catalog) -> None:
    root, leaf, consumer = release("root"), release("leaf"), release("consumer")
    catalog.publish(leaf)
    catalog.publish(root, dependencies=(leaf.ref,))
    graph = resolve_published(catalog, root.ref)
    before = graph_json(graph)
    catalog.publish(consumer, dependencies=(root.ref,))
    assert graph_json(resolve_published(catalog, root.ref)) == before
    assert catalog.find_dependents(root.ref, candidates=SearchQuery(), transitive=False) == (
        consumer,
    )


@pytest.mark.parametrize("length", [1, 40])
def test_resolution_returns_the_entire_graph(catalog: Catalog, length: int) -> None:
    records = tuple(release(f"part-{index:04}") for index in range(length + 1))
    edges = tuple(Dependency(source.ref, target.ref) for source, target in pairwise(records))
    catalog.publish(records[-1])
    for source, target in reversed(tuple(pairwise(records))):
        catalog.publish(source, dependencies=(target.ref,))
    result = resolve_published(catalog, records[0].ref)
    assert result.records == records
    assert set(result.dependencies) == set(edges)


def test_missing_graph_roots_are_errors(catalog: Catalog) -> None:
    ref = release("missing").ref
    with pytest.raises(NotFound):
        catalog.dependencies(ref)
    with pytest.raises(NotFound):
        catalog.find_dependents(ref, candidates=SearchQuery(), transitive=False)


def test_payload_changes_do_not_change_the_publication(catalog: Catalog) -> None:
    nested: list[JSON] = [1, {"nested": [2]}]
    item = Record(release("data").ref, {"items": nested})
    catalog.publish(item)
    nested.clear()
    stored = catalog.get(item.ref)
    assert stored.payload == {"items": (1, {"nested": (2,)})}
