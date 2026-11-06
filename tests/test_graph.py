"""Each project description has its own resolver and lockfile scope."""

from dataclasses import replace
from pathlib import Path

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import CatalogError, InvalidPackage
from hwcatalog.api.graph import Graph
from hwcatalog.api.records import Record, Ref
from tests.helpers import local_record


def test_graphs_with_separate_inputs_and_locks_share_a_workspace(
    catalog: Catalog, tmp_path: Path
) -> None:
    first = catalog.publish(
        local_record(Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)))
    )
    second = catalog.publish(
        local_record(Record(replace(first.ref, version="2.0", catalog=catalog)))
    )
    root = Record(Ref("soc", "top", "rtl", "default", "1.0", catalog=catalog))
    left = Graph(
        catalog, local_record(root), dependencies=(first.ref,), lockfile=tmp_path / "left.lock"
    )
    right = Graph(
        catalog,
        local_record(replace(root, ref=replace(root.ref, component="other", catalog=catalog))),
        dependencies=(second.ref,),
    )
    left_path = left.create_lockfile()
    left_lock = left_path.read_bytes()
    right_path = right.create_lockfile(tmp_path / "right.lock")
    assert left_path != right_path
    assert left.lockfile == left_path
    assert right.lockfile == right_path
    assert {record.ref for record in left.resolve().records} == {root.ref, first.ref}
    assert {record.ref for record in right.resolve().records} == {right.record.ref, second.ref}
    right.create_lockfile()
    assert left_path.read_bytes() == left_lock
    assert catalog.search() == (first, second)


def test_graph_lockfile_requires_a_path(catalog: Catalog) -> None:
    graph = Graph(
        catalog, local_record(Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)))
    )
    with pytest.raises(CatalogError, match="lockfile path"):
        graph.create_lockfile()


def test_failed_lock_creation_preserves_previous_scope(catalog: Catalog, tmp_path: Path) -> None:
    parent = tmp_path / "directory"
    parent.mkdir()
    graph = Graph(
        catalog, local_record(Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)))
    )
    original = graph.create_lockfile(parent / "conan.lock")
    graph.dependencies = (Ref("soc", "absent", "rtl", "default", "1.0", catalog=catalog),)
    with pytest.raises(CatalogError):
        graph.create_lockfile(tmp_path / "new.lock")
    assert graph.lockfile == original


def test_legacy_coordinate_names_are_rejected(*, catalog: Catalog) -> None:
    from hwcatalog._internal.conan.format import from_conan_reference

    with pytest.raises(InvalidPackage):
        from_conan_reference("soc.cpu.rtl/1.0", catalog=catalog)
