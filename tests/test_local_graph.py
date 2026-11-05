"""Resolve unpublished consumers using the native Conan graph API."""

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from examples.conan_config import configured_home
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import DependencyCycle, InvalidPackage, NotFound, VersionConflict
from hwcatalog.api.models.graph import Dependency
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.backends.conan import ConanBackend


@pytest.fixture
def local_catalog(tmp_path: Path) -> Iterator[Catalog]:
    backend = ConanBackend(configured_home(tmp_path / "cache"))
    with Catalog(backend) as catalog:
        yield catalog


def record(name: str, version: str = "1.0") -> Record:
    return Record(Ref(name, version, "rtl", "soc"))


def test_unpublished_consumer_resolves_shared_dependencies(local_catalog: Catalog) -> None:
    catalog = local_catalog
    leaf, left, right = (record(name) for name in ("leaf", "left", "right"))
    catalog.publish(leaf)
    for branch in (left, right):
        catalog.publish(branch, dependencies=(leaf.ref,))
    root = Record(record("top").ref, {"local": ["working-tree"]})
    graph = catalog.graph.resolve_local(root, dependencies=(left.ref, right.ref))
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
        catalog.get(root.ref)
    assert len(catalog.search()) == 3


def test_local_consumer_with_no_dependencies(local_catalog: Catalog) -> None:
    root = record("top")
    graph = local_catalog.graph.resolve_local(root)
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
    graph = catalog.graph.resolve_local(local, dependencies=(current.ref,))
    assert graph.records == (current, local)
    assert graph.dependencies == (Dependency(root.ref, current.ref),)
    assert catalog.get(root.ref) == root
    assert catalog.graph.dependencies(root.ref) == (old,)


def test_local_graph_checks_versions_across_branches(local_catalog: Catalog) -> None:
    catalog = local_catalog
    first, second = record("leaf"), record("leaf", "2.0")
    left, right, root = (record(name) for name in ("left", "right", "top"))
    for item in (first, second):
        catalog.publish(item)
    catalog.publish(left, dependencies=(first.ref,))
    catalog.publish(right, dependencies=(second.ref,))
    with pytest.raises(VersionConflict):
        catalog.graph.resolve_local(root, dependencies=(left.ref, right.ref))


def test_local_graph_rejects_cycles_and_missing_dependencies(local_catalog: Catalog) -> None:
    catalog = local_catalog
    root, child = record("top"), record("child")
    with pytest.raises(DependencyCycle):
        catalog.graph.resolve_local(root, dependencies=(root.ref,))
    with pytest.raises(NotFound):
        catalog.graph.resolve_local(root, dependencies=(child.ref,))
    catalog.publish(root)
    catalog.publish(child, dependencies=(root.ref,))
    with pytest.raises(DependencyCycle):
        catalog.graph.resolve_local(root, dependencies=(child.ref,))


def test_conan_resolves_one_consumer_without_exporting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = ConanBackend(configured_home(tmp_path / "cache"))
    with Catalog(backend) as catalog:
        dependencies = tuple(record(name) for name in ("left", "right"))
        for item in dependencies:
            catalog.publish(item)
        operations: list[tuple[str, ...]] = []
        original_run = backend._client.run

        def track(*arguments: str) -> object:
            operations.append(arguments)
            return original_run(*arguments)

        monkeypatch.setattr(backend._client, "run", track)
        catalog.graph.resolve_local(record("top"), dependencies=tuple(d.ref for d in dependencies))
        graphs = [args for args in operations if args[:2] == ("graph", "info")]
        assert len(graphs) == 1
        assert not Path(graphs[0][2]).exists()  # Temporary consumer is removed.
        assert not {args[0] for args in operations} & {"export", "export-pkg", "upload"}
        with pytest.raises(NotFound):
            catalog.get(record("top").ref)


def test_conan_local_consumer_uses_opaque_project_lockfile(tmp_path: Path) -> None:
    backend = ConanBackend(configured_home(tmp_path / "cache"))
    with Catalog(backend) as catalog:
        leaf, child, root = (record(name) for name in ("leaf", "child", "top"))
        catalog.publish(leaf)
        catalog.publish(child, dependencies=(leaf.ref,))
        lockfile = catalog.create_lockfile((child.ref,), tmp_path / "project/conan.lock")
        original = lockfile.read_bytes()
        expected = catalog.graph.resolve_local(root, dependencies=(child.ref,))
        assert (
            catalog.graph.resolve_local(root, dependencies=(child.ref,), lockfile=lockfile)
            == expected
        )
        assert lockfile.read_bytes() == original
        backend._client.run(
            "lock",
            "remove",
            "--requires=soc.leaf.rtl/*",
            "--lockfile",
            str(lockfile),
            "--lockfile-out",
            str(lockfile),
        )
        incomplete = lockfile.read_bytes()
        with pytest.raises(InvalidPackage, match="absent from its lockfile"):
            catalog.graph.resolve_local(root, dependencies=(child.ref,), lockfile=lockfile)
        assert lockfile.read_bytes() == incomplete
