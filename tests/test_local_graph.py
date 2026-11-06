"""Resolve unpublished consumers using the native Conan graph API."""

from dataclasses import replace
from pathlib import Path

import pytest

from examples.conan_config import configured_home
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import Conflict, InvalidPackage, NotFound
from hwcatalog.api.graph import Dependency, Graph
from hwcatalog.api.records import Record, Ref


@pytest.fixture
def local_catalog(tmp_path: Path) -> Catalog:
    return Catalog(configured_home(tmp_path / "cache"))


def record(name: str, version: str = "1.0") -> Record:
    return Record(Ref("soc", name, "rtl", "default", version))


def test_unpublished_consumer_resolves_shared_dependencies(local_catalog: Catalog) -> None:
    catalog = local_catalog
    leaf, left, right = (record(name) for name in ("leaf", "left", "right"))
    catalog.publish(leaf)
    for branch in (left, right):
        catalog.publish(branch, dependencies=(leaf.ref,))
    root = Record(record("top").ref, {"local": ["working-tree"]})
    graph = Graph(catalog, root, dependencies=(left.ref, right.ref)).resolve()
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
    root = record("top")
    graph = Graph(local_catalog, root).resolve()
    assert graph.records == (root,)
    assert graph.dependencies == ()
    assert local_catalog.search() == ()


def test_local_data_overrides_published_root(local_catalog: Catalog) -> None:
    catalog = local_catalog
    old, current, root = (record(name) for name in ("old", "current", "top"))
    catalog.publish(old)
    catalog.publish(current)
    catalog.publish(root, dependencies=(old.ref,))
    local = replace(root, payload={"local": True})
    graph = Graph(catalog, local, dependencies=(current.ref,)).resolve()
    assert graph.records == (current, local)
    assert graph.dependencies == (Dependency(root.ref, current.ref),)
    assert Ref(**root.ref.to_dict(), catalog=catalog).get() == root
    assert Ref(**root.ref.to_dict(), catalog=catalog).dependencies() == (old,)


def test_local_graph_checks_versions_across_branches(local_catalog: Catalog) -> None:
    catalog = local_catalog
    first, second = record("leaf"), record("leaf", "2.0")
    left, right, root = (record(name) for name in ("left", "right", "top"))
    for item in (first, second):
        catalog.publish(item)
    catalog.publish(left, dependencies=(first.ref,))
    catalog.publish(right, dependencies=(second.ref,))
    with pytest.raises(Conflict):
        Graph(catalog, root, dependencies=(left.ref, right.ref)).resolve()


def test_local_graph_rejects_cycles_and_missing_dependencies(local_catalog: Catalog) -> None:
    catalog = local_catalog
    root, child = record("top"), record("child")
    with pytest.raises(InvalidPackage):
        Graph(catalog, root, dependencies=(root.ref,)).resolve()
    with pytest.raises(NotFound):
        Graph(catalog, root, dependencies=(child.ref,)).resolve()
    catalog.publish(root)
    catalog.publish(child, dependencies=(root.ref,))
    with pytest.raises(InvalidPackage):
        Graph(catalog, root, dependencies=(child.ref,)).resolve()


def test_conan_resolves_one_consumer_without_exporting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = Catalog(configured_home(tmp_path / "cache"))
    catalog = connection
    dependencies = tuple(record(name) for name in ("left", "right"))
    for item in dependencies:
        catalog.publish(item)
    operations: list[tuple[str, ...]] = []
    original_run = connection._client.run

    def track(*arguments: str) -> object:
        operations.append(arguments)
        return original_run(*arguments)

    monkeypatch.setattr(connection._client, "run", track)
    Graph(catalog, record("top"), dependencies=tuple(d.ref for d in dependencies)).resolve()
    graphs = [args for args in operations if args[:2] == ("graph", "info")]
    assert len(graphs) == 1
    assert not Path(graphs[0][2]).exists()  # Temporary consumer is removed.
    assert not {args[0] for args in operations} & {"export", "export-pkg", "upload"}
    with pytest.raises(NotFound):
        Ref(**record("top").ref.to_dict(), catalog=catalog).get()


def test_conan_local_consumer_uses_opaque_project_lockfile(tmp_path: Path) -> None:
    connection = Catalog(configured_home(tmp_path / "cache"))
    catalog = connection
    leaf, child, root = (record(name) for name in ("leaf", "child", "top"))
    catalog.publish(leaf)
    catalog.publish(child, dependencies=(leaf.ref,))
    lockfile = Graph(catalog, root, dependencies=(child.ref,)).create_lockfile(
        tmp_path / "project/conan.lock"
    )
    original = lockfile.read_bytes()
    expected = Graph(catalog, root, dependencies=(child.ref,)).resolve()
    assert Graph(catalog, root, dependencies=(child.ref,), lockfile=lockfile).resolve() == expected
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
        Graph(catalog, root, dependencies=(child.ref,), lockfile=lockfile).resolve()
    assert lockfile.read_bytes() == incomplete
