"""The public contract contains immutable records and exact dependencies."""

from __future__ import annotations

from itertools import pairwise
from typing import cast

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import InvalidPackage, NotFound
from coreref.api.query import SearchQuery
from coreref.api.records import JSON, LocalRecord, Record, Ref
from tests.helpers import graph_json, local_record, release, resolve_published


def test_get_returns_published_records_and_reports_missing(catalog: Catalog) -> None:
    first, second = release("first", catalog=catalog), release("second", catalog=catalog)
    second = catalog.publish(local_record(second))
    first = catalog.publish(local_record(first))
    assert Ref.from_fields(**first.ref.to_dict(), catalog=catalog).get() == first
    assert Ref.from_fields(**second.ref.to_dict(), catalog=catalog).get() == second
    with pytest.raises(NotFound):
        Ref.from_fields(**release("absent", catalog=catalog).ref.to_dict(), catalog=catalog).get()
    assert catalog.search(SearchQuery()) == (first, second)


def test_new_revision_preserves_previously_published_records(catalog: Catalog) -> None:
    original = release("existing", catalog=catalog)
    original = catalog.publish(local_record(original))
    changed = catalog.publish(local_record(Record(original.ref, {"changed": True})))
    assert changed.ref != original.ref
    assert catalog.search(SearchQuery()) == (changed,)
    assert Ref.from_fields(**original.ref.to_dict(), catalog=catalog).get() == original


@pytest.mark.parametrize("count", [0, 1, 2])
def test_publish_rejects_record_collections(catalog: Catalog, count: int) -> None:
    records = tuple(release(f"record-{index}", catalog=catalog) for index in range(count))
    with pytest.raises(InvalidPackage, match="one LocalRecord"):
        catalog.publish(cast(LocalRecord, records))
    assert not catalog.search(SearchQuery())


@pytest.mark.parametrize("problem", ["duplicate", "missing", "self", "edge-value"])
def test_invalid_dependencies_do_not_publish(catalog: Catalog, problem: str) -> None:
    source, target = release("source", catalog=catalog), release("target", catalog=catalog)
    target = catalog.publish(local_record(target))
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
        dependencies = (cast(Ref, (source.ref, target.ref)),)
    with pytest.raises(exception):
        source = catalog.publish(local_record(source), dependencies=dependencies)
    assert catalog.search(SearchQuery()) == (target,)


def test_dependencies_must_already_exist(catalog: Catalog) -> None:
    first, second = release("first", catalog=catalog), release("second", catalog=catalog)
    with pytest.raises(NotFound):
        first = catalog.publish(local_record(first), dependencies=(second.ref,))
    second = catalog.publish(local_record(second))
    first = catalog.publish(local_record(first), dependencies=(second.ref,))
    with pytest.raises(InvalidPackage, match="contains a cycle"):
        catalog.publish(local_record(second), dependencies=(first.ref,))
    assert tuple(sorted(resolve_published(catalog, first.ref).networkx.edges)) == (
        (first.ref, second.ref),
    )


def test_diamond_has_one_record_per_ref_and_exact_edges(catalog: Catalog) -> None:
    root, left, right, shared = (
        release(name, catalog=catalog) for name in ("root", "left", "right", "shared")
    )
    shared = catalog.publish(local_record(shared))
    left = catalog.publish(local_record(left), dependencies=(shared.ref,))
    right = catalog.publish(local_record(right), dependencies=(shared.ref,))
    root = catalog.publish(local_record(root), dependencies=(left.ref, right.ref))
    edges = (
        (root.ref, left.ref),
        (root.ref, right.ref),
        (left.ref, shared.ref),
        (right.ref, shared.ref),
    )
    graph = resolve_published(catalog, root.ref)
    assert {record.ref for record in graph.records} == {
        root.ref,
        left.ref,
        right.ref,
        shared.ref,
    }
    assert tuple(sorted(graph.networkx.edges)) == tuple(sorted(edges))
    assert Ref.from_fields(**root.ref.to_dict(), catalog=catalog).graph().dependencies() == (
        left,
        right,
    )
    assert Ref.from_fields(**root.ref.to_dict(), catalog=catalog).graph().dependencies(
        filters=SearchQuery(component="left")
    ) == (left,)
    assert (
        not Ref.from_fields(**root.ref.to_dict(), catalog=catalog)
        .graph()
        .dependencies(filters=SearchQuery(component="absent"))
    )
    assert resolve_published(catalog, shared.ref).records == (shared,)


def test_resolution_is_stable_when_new_consumers_are_published(catalog: Catalog) -> None:
    root, leaf, consumer = (
        release("root", catalog=catalog),
        release("leaf", catalog=catalog),
        release("consumer", catalog=catalog),
    )
    leaf = catalog.publish(local_record(leaf))
    root = catalog.publish(local_record(root), dependencies=(leaf.ref,))
    graph = resolve_published(catalog, root.ref)
    before = graph_json(graph)
    consumer = catalog.publish(local_record(consumer), dependencies=(root.ref,))
    assert graph_json(resolve_published(catalog, root.ref)) == before


@pytest.mark.parametrize("length", [1, 40])
def test_resolution_returns_the_entire_graph(catalog: Catalog, length: int) -> None:
    records = tuple(release(f"part-{index:04}", catalog=catalog) for index in range(length + 1))
    catalog.publish(local_record(records[-1]))
    for source, target in reversed(tuple(pairwise(records))):
        catalog.publish(local_record(source), dependencies=(target.ref,))
    records = tuple(record.ref.get() for record in records)
    edges = tuple((source.ref, target.ref) for source, target in pairwise(records))
    result = resolve_published(catalog, records[0].ref)
    assert result.records == records
    assert set(result.networkx.edges) == set(edges)


def test_missing_graph_roots_are_errors(catalog: Catalog) -> None:
    ref = release("missing", catalog=catalog).ref
    with pytest.raises(NotFound):
        Ref.from_fields(**ref.to_dict(), catalog=catalog).graph().dependencies()


def test_payload_changes_do_not_change_the_publication(catalog: Catalog) -> None:
    nested: list[JSON] = [1, {"nested": [2]}]
    item = Record(release("data", catalog=catalog).ref, {"items": nested})
    item = catalog.publish(local_record(item))
    nested.clear()
    stored = Ref.from_fields(**item.ref.to_dict(), catalog=catalog).get()
    assert stored.payload == {"items": (1, {"nested": (2,)})}
