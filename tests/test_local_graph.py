"""Resolve unpublished consumers using the native Conan graph API."""

from dataclasses import replace
from pathlib import Path

import pytest

from examples.conan_config import configured_home
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import Conflict, InvalidPackage, NotFound
from hwcatalog.api.graph import Dependency, Graph
from hwcatalog.api.records import Record, Ref
from tests.helpers import local_record


@pytest.fixture
def local_catalog(tmp_path: Path) -> Catalog:
    return Catalog(configured_home(tmp_path / "cache"))


def record(name: str, version: str = "1.0", *, catalog: Catalog) -> Record:
    return Record(Ref("soc", name, "rtl", "default", version, catalog=catalog))


def test_unpublished_consumer_resolves_shared_dependencies(local_catalog: Catalog) -> None:
    catalog = local_catalog
    leaf, left, right = (record(name, catalog=local_catalog) for name in ("leaf", "left", "right"))
    catalog.publish(local_record(leaf))
    for branch in (left, right):
        catalog.publish(local_record(branch), dependencies=(leaf.ref,))
    root = Record(record("top", catalog=local_catalog).ref, {"local": ["working-tree"]})
    graph = Graph(catalog, local_record(root), dependencies=(left.ref, right.ref)).result
    assert {item.ref for item in graph.records} == {root.ref, leaf.ref, left.ref, right.ref}
    assert set(graph.dependencies) == {
        Dependency(root.ref, left.ref),
        Dependency(root.ref, right.ref),
        Dependency(left.ref, leaf.ref),
        Dependency(right.ref, leaf.ref),
    }
    stored_root = next(item for item in graph.records if item.ref == root.ref)
    assert stored_root.payload == {"local": ("working-tree",)}
    with pytest.raises(NotFound):
        Ref(**root.ref.to_dict(), catalog=catalog).get()
    assert len(catalog.search()) == 3


def test_local_consumer_with_no_dependencies(local_catalog: Catalog) -> None:
    root = record("top", catalog=local_catalog)
    graph = Graph(local_catalog, local_record(root)).result
    assert graph.records == (local_record(root),)
    assert graph.dependencies == ()
    assert local_catalog.search() == ()


def test_local_data_overrides_published_root(local_catalog: Catalog) -> None:
    catalog = local_catalog
    old, current, root = (record(name, catalog=local_catalog) for name in ("old", "current", "top"))
    catalog.publish(local_record(old))
    catalog.publish(local_record(current))
    catalog.publish(local_record(root), dependencies=(old.ref,))
    local = replace(root, payload={"local": True})
    graph = Graph(catalog, local_record(local), dependencies=(current.ref,)).result
    assert graph.records == (current, local_record(local))
    assert graph.dependencies == (Dependency(root.ref, current.ref),)
    assert Ref(**root.ref.to_dict(), catalog=catalog).get() == root
    assert Ref(**root.ref.to_dict(), catalog=catalog).dependencies() == (old,)


def test_local_graph_checks_versions_across_branches(local_catalog: Catalog) -> None:
    catalog = local_catalog
    first, second = (
        record("leaf", catalog=local_catalog),
        record("leaf", "2.0", catalog=local_catalog),
    )
    left, right, root = (record(name, catalog=local_catalog) for name in ("left", "right", "top"))
    for item in (first, second):
        catalog.publish(local_record(item))
    catalog.publish(local_record(left), dependencies=(first.ref,))
    catalog.publish(local_record(right), dependencies=(second.ref,))
    with pytest.raises(Conflict):
        Graph(catalog, local_record(root), dependencies=(left.ref, right.ref))


def test_local_graph_rejects_cycles_and_missing_dependencies(local_catalog: Catalog) -> None:
    catalog = local_catalog
    root, child = record("top", catalog=local_catalog), record("child", catalog=local_catalog)
    with pytest.raises(InvalidPackage):
        Graph(catalog, local_record(root), dependencies=(root.ref,))
    with pytest.raises(NotFound):
        Graph(catalog, local_record(root), dependencies=(child.ref,))
    catalog.publish(local_record(root))
    catalog.publish(local_record(child), dependencies=(root.ref,))
    with pytest.raises(InvalidPackage):
        Graph(catalog, local_record(root), dependencies=(child.ref,))


def test_conan_resolves_one_consumer_without_exporting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, catalog: Catalog
) -> None:
    connection = Catalog(configured_home(tmp_path / "cache"))
    catalog = connection
    dependencies = tuple(record(name, catalog=catalog) for name in ("left", "right"))
    for item in dependencies:
        catalog.publish(local_record(item))
    operations: list[tuple[str, ...]] = []
    original_run = connection._client.run

    def track(*arguments: str) -> object:
        operations.append(arguments)
        return original_run(*arguments)

    monkeypatch.setattr(connection._client, "run", track)
    Graph(
        catalog,
        local_record(record("top", catalog=catalog)),
        dependencies=tuple(d.ref for d in dependencies),
    )
    graphs = [args for args in operations if args[:2] == ("graph", "info")]
    assert len(graphs) == 1
    assert not Path(graphs[0][2]).exists()  # Temporary consumer is removed.
    assert not {args[0] for args in operations} & {"export", "export-pkg", "upload"}
    with pytest.raises(NotFound):
        Ref(**record("top", catalog=catalog).ref.to_dict(), catalog=catalog).get()


def test_conan_local_consumer_uses_opaque_project_lockfile(
    tmp_path: Path, *, catalog: Catalog
) -> None:
    connection = Catalog(configured_home(tmp_path / "cache"))
    catalog = connection
    leaf, child, root = (record(name, catalog=catalog) for name in ("leaf", "child", "top"))
    catalog.publish(local_record(leaf))
    catalog.publish(local_record(child), dependencies=(leaf.ref,))
    lockfile = Graph(catalog, local_record(root), dependencies=(child.ref,)).create_lockfile(
        tmp_path / "project/conan.lock"
    )
    original = lockfile.read_bytes()
    expected = Graph(catalog, local_record(root), dependencies=(child.ref,)).result
    assert (
        Graph(catalog, local_record(root), dependencies=(child.ref,), lockfile=lockfile).result
        == expected
    )
    assert lockfile.read_bytes() == original
    connection._client.run(
        "lock",
        "remove",
        "--requires=soc.leaf.rtl.default/*",
        "--lockfile",
        str(lockfile),
        "--lockfile-out",
        str(lockfile),
    )
    incomplete = lockfile.read_bytes()
    with pytest.raises(InvalidPackage, match="absent from its lockfile"):
        Graph(catalog, local_record(root), dependencies=(child.ref,), lockfile=lockfile)
    assert lockfile.read_bytes() == incomplete
