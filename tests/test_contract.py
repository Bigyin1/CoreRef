"""The public contract contains immutable records and exact dependencies."""

from collections.abc import Callable
from dataclasses import replace
from itertools import pairwise
from typing import cast

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import DependencyCycle, IdentityConflict, InvalidPublication, NotFound
from hwcatalog.api.models.graph import Dependency
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.api.types import JSON
from tests.helpers import graph_json, release


def test_get_many_preserves_order_duplicates_and_missing(catalog: Catalog) -> None:
    first, second = release("first"), release("second")
    catalog.publish((second, first))
    assert catalog.get_many((first.ref, second.ref, first.ref)) == (first, second, first)
    assert catalog.get_many(()) == ()
    with pytest.raises(NotFound):
        catalog.get_many((first.ref, release("absent").ref))
    assert catalog.search() == (first, second)


def test_publication_is_immutable_and_conflicts_are_atomic(catalog: Catalog) -> None:
    original, new = release("existing"), release("new")
    catalog.publish((original,))
    with pytest.raises(IdentityConflict):
        catalog.publish((new, replace(original, payload={"changed": True})))
    assert catalog.search() == (original,)
    assert catalog.get(original.ref) == original


@pytest.mark.parametrize(
    "problem",
    ["duplicate-record", "duplicate-edge", "missing-target", "old-source", "self", "wrong-edge"],
)
def test_invalid_publications_do_not_write(catalog: Catalog, problem: str) -> None:
    first, second = release("first"), release("second")
    edge = Dependency(first.ref, second.ref)
    records: tuple[Record, ...] = (first, second)
    edges: tuple[Dependency, ...] = (edge,)
    if problem == "duplicate-record":
        records = (first, first)
    elif problem == "duplicate-edge":
        edges = (edge, edge)
    elif problem == "missing-target":
        records = (first,)
    elif problem == "old-source":
        catalog.publish((first,))
        records = (second,)
    elif problem == "self":
        edges = (Dependency(first.ref, first.ref),)
    else:
        edges = (cast("Dependency", None),)
    before = catalog.search()
    with pytest.raises(InvalidPublication):
        catalog.publish(records, dependencies=edges)
    assert catalog.search() == before


def test_dependency_cycles_do_not_publish(catalog: Catalog) -> None:
    first, second, third = (release(name) for name in ("first", "second", "third"))
    with pytest.raises(DependencyCycle):
        catalog.publish(
            (first, second, third),
            dependencies=(
                Dependency(first.ref, second.ref),
                Dependency(second.ref, third.ref),
                Dependency(third.ref, first.ref),
            ),
        )
    assert catalog.search() == ()


def test_diamond_has_one_record_per_ref_and_exact_edges(catalog: Catalog) -> None:
    root, left, right, shared = (release(name) for name in ("root", "left", "right", "shared"))
    edges = (
        Dependency(root.ref, left.ref),
        Dependency(root.ref, right.ref),
        Dependency(left.ref, shared.ref),
        Dependency(right.ref, shared.ref),
    )
    catalog.publish((right, shared, root, left), dependencies=edges[::-1])
    graph = catalog.graph.dependency_closure(root.ref)
    assert {record.ref for record in graph.records} == {root.ref, left.ref, right.ref, shared.ref}
    assert graph.dependencies == tuple(sorted(edges, key=lambda edge: edge.to_json()))
    assert catalog.graph.dependencies(root.ref) == (left, right)
    assert catalog.graph.consumers(shared.ref) == (left, right)
    assert catalog.graph.dependencies(root.ref, SearchQuery(name="left")) == (left,)
    assert catalog.graph.consumers(shared.ref, SearchQuery(name="right")) == (right,)
    assert not catalog.graph.dependencies(root.ref, SearchQuery(name="absent"))
    assert catalog.graph.dependency_closure(shared.ref).records == (shared,)
    assert graph.consistency == "snapshot"


def test_closure_is_stable_when_new_consumers_are_published(catalog: Catalog) -> None:
    root, leaf, consumer = release("root"), release("leaf"), release("consumer")
    catalog.publish((root, leaf), dependencies=(Dependency(root.ref, leaf.ref),))
    graph = catalog.graph.dependency_closure(root.ref)
    before = graph_json(graph)
    catalog.publish((consumer,), dependencies=(Dependency(consumer.ref, root.ref),))
    assert graph_json(catalog.graph.dependency_closure(root.ref)) == before
    assert catalog.graph.consumers(root.ref) == (consumer,)


@pytest.mark.parametrize("length", [40, 1001])
def test_dependency_closure_returns_the_entire_graph(catalog: Catalog, length: int) -> None:
    records = tuple(release(f"part-{index:04}") for index in range(length + 1))
    edges = tuple(Dependency(source.ref, target.ref) for source, target in pairwise(records))
    catalog.publish(records[::-1], dependencies=edges[::-1])
    result = catalog.graph.dependency_closure(records[0].ref)
    assert result.records == records
    assert set(result.dependencies) == set(edges)


def test_missing_graph_roots_are_errors(catalog: Catalog) -> None:
    ref = release("missing").ref
    methods: tuple[Callable[[Ref], object], ...] = (
        catalog.graph.dependencies,
        catalog.graph.consumers,
        catalog.graph.dependency_closure,
    )
    for method in methods:
        with pytest.raises(NotFound):
            method(ref)


def test_payload_changes_do_not_change_the_publication(catalog: Catalog) -> None:
    nested: list[JSON] = [1, {"nested": [2]}]
    item = Record(release("data").ref, {"items": nested})
    catalog.publish((item,))
    nested.clear()
    stored = catalog.get(item.ref)
    assert stored.payload == {"items": (1, {"nested": (2,)})}
