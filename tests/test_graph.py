"""Each project description has its own resolver and lockfile scope."""

from __future__ import annotations

from pathlib import Path

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError, InvalidPackage
from coreref.api.graph import Graph
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord, Record, Ref
from tests.helpers import local_record


def test_graphs_with_separate_inputs_and_locks_share_a_workspace(
    catalog: Catalog, tmp_path: Path
) -> None:
    first = catalog.publish(
        local_record(
            Record(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
        )
    )
    second = catalog.publish(
        local_record(
            Record(Ref.from_fields(**(first.ref.to_dict() | {"version": "2.0"}), catalog=catalog))
        )
    )
    root = Record(Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=catalog))
    left = Graph(catalog, local_record(root), dependencies=(first.ref,))
    right = Graph(
        catalog,
        local_record(
            Record(
                Ref.from_fields(**(root.ref.to_dict() | {"component": "other"}), catalog=catalog),
                root.payload,
            )
        ),
        dependencies=(second.ref,),
    )
    left_path = left.create_lockfile(tmp_path / "left.lock")
    left_lock = left_path.read_bytes()
    right_path = right.create_lockfile(tmp_path / "right.lock")
    assert left_path != right_path
    assert left.lockfile == left_path
    assert right.lockfile == right_path
    assert {record.ref for record in left.records} == {root.ref, first.ref}
    assert {record.ref for record in right.records} == {right.record.ref, second.ref}
    right.create_lockfile()
    assert left_path.read_bytes() == left_lock
    assert catalog.search(SearchQuery()) == (first, second)


def test_graph_lockfile_requires_a_path(catalog: Catalog) -> None:
    graph = Graph(
        catalog,
        local_record(
            Record(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
        ),
    )
    with pytest.raises(CatalogError, match="lockfile path"):
        graph.create_lockfile()


def test_failed_lock_creation_preserves_previous_scope(catalog: Catalog, tmp_path: Path) -> None:
    parent = tmp_path / "directory"
    parent.mkdir()
    graph = Graph(
        catalog,
        local_record(
            Record(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
        ),
    )
    original = graph.create_lockfile(parent / "conan.lock")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    with pytest.raises(CatalogError):
        graph.create_lockfile(blocked)
    assert graph.lockfile == original


def test_legacy_coordinate_names_are_rejected(*, catalog: Catalog) -> None:
    from coreref._internal.references import from_conan_reference

    with pytest.raises(InvalidPackage):
        from_conan_reference("soc.cpu.rtl/1.0", catalog=catalog)


def test_lockfile_uses_the_resolved_snapshot_without_more_conan_commands(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dependency = catalog.publish(
        local_record(
            Record(Ref.from_fields("soc", "leaf", "rtl", "default", "1.0", catalog=catalog))
        )
    )
    local = local_record(
        Record(Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=catalog))
    )
    graph = Graph(catalog, local, dependencies=(dependency.ref,))
    records, edges = graph.records, graph.edges

    def reject(*_arguments: str) -> object:
        raise AssertionError("Saving a resolved graph must not issue more Conan commands")

    with monkeypatch.context() as patch:
        patch.setattr(catalog._client, "run", reject)
        lockfile = graph.create_lockfile(tmp_path / "conan.lock")
        assert graph.create_lockfile() == lockfile
    assert graph.records == records and graph.edges is edges
    assert Graph(catalog, local, dependencies=(dependency.ref,), lockfile=lockfile) == graph


@pytest.mark.parametrize(
    "attribute",
    [
        "catalog",
        "record",
        "records",
        "edges",
        "requirements",
        "networkx",
        "lockfile",
        "editables",
    ],
)
def test_resolved_structure_cannot_be_reassigned(catalog: Catalog, attribute: str) -> None:
    local = LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=catalog))
    graph = Graph(catalog, local)
    expected = graph.ordered_refs(local.ref)
    with pytest.raises(AttributeError):
        setattr(graph, attribute, None)
    assert graph.ordered_refs(local.ref) is expected
    assert graph.record == local


def test_local_views_cannot_change_the_resolved_root(catalog: Catalog) -> None:
    local = LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=catalog))
    graph = Graph(catalog, local)
    expected = graph.ordered_refs(local.ref)
    views = (graph.record, graph.records[0], graph.ordered_records(local.ref)[0])
    for record in views:
        assert isinstance(record, LocalRecord)
        record.ref = Ref.from_fields("soc", "other", "rtl", "default", "1.0", catalog=catalog)
        record.payload = {"changed": True}
    assert graph.record.ref == local.ref
    assert graph.records == (local,)
    assert graph.ordered_records(local.ref) == (local,)
    assert graph.ordered_refs(local.ref) is expected
    assert graph.networkx is graph.networkx
    assert tuple(graph.networkx) == (local.ref,)
