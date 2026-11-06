"""Behavioral tests against real Conan recipes and its dependency graph evaluator."""

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from examples.conan_config import configured_home
from hwcatalog._internal.conan import graph as conan_graph
from hwcatalog._internal.conan import records as conan_records
from hwcatalog._internal.conan.format import (
    from_conan_reference,
    to_conan_reference,
    write_recipe,
)
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import CatalogError, Conflict, InvalidPackage, NotFound
from hwcatalog.api.graph import Dependency, Graph
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import Record, Ref
from tests.helpers import resolve_published

pytestmark = pytest.mark.conan


@pytest.fixture
def conan_catalog(tmp_path: Path) -> Catalog:
    return Catalog(configured_home(tmp_path / "home"))


def record(
    kind: str = "rtl", version: str = "1.0", name: str = "cpu", *, package_type: str = "default"
) -> Record:
    return Record(Ref("soc", name, kind, package_type, version), {"files": [f"{name}.{kind}"]})


@pytest.mark.parametrize("field", ["namespace", "component", "view", "type", "version"])
@pytest.mark.parametrize("value", ["with.dot", "with/slash", "Upper", "", "*", "a b"])
def test_conan_coordinates_reject_lossy_conversion(field: str, value: str) -> None:
    if field == "version" and value == "with.dot":
        return
    with pytest.raises(InvalidPackage):
        to_conan_reference(replace(record().ref, **{field: value}, catalog=None))


def test_reference_round_trip() -> None:
    ref = record("be", "4.0", package_type="lib-lef").ref
    assert to_conan_reference(ref) == "soc.cpu.be.lib-lef/4.0"
    assert from_conan_reference("soc.cpu.be.lib-lef/4.0#" + "a" * 32) == ref
    with pytest.raises(InvalidPackage):
        from_conan_reference("ordinary-library/1.0@user/channel")
    with pytest.raises(InvalidPackage):
        to_conan_reference(replace(ref, component="a" * 101))
    with pytest.raises(InvalidPackage):
        to_conan_reference(replace(ref, namespace="-invalid"))


def test_publish_uses_one_export_pkg_without_graph_or_export_commands(
    conan_catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    operations: list[tuple[str, ...]] = []
    original_run = conan_catalog._client.run

    def track(*arguments: str) -> object:
        operations.append(arguments)
        return original_run(*arguments)

    monkeypatch.setattr(conan_catalog._client, "run", track)
    published = conan_catalog.publish(record())
    assert published.ref == record().ref
    assert sum(arguments[0] == "export-pkg" for arguments in operations) == 1
    assert not any(arguments[0] == "export" for arguments in operations)
    assert not any(arguments[:2] == ("graph", "info") for arguments in operations)


def test_publish_does_not_scan_unrelated_packages(conan_catalog: Catalog, tmp_path: Path) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "conanfile.py").write_text(
        "from conan import ConanFile\nclass Foreign(ConanFile):\n"
        '    name = "external.vendor.library.helper"\n    version = "1.0"\n'
    )
    conan_catalog._client.export(foreign)
    published = conan_catalog.publish(record())
    assert Ref(**published.ref.to_dict(), catalog=conan_catalog).get() == published


def test_publish_rejects_an_invalid_catalog_dependency_before_conan_executes_it(
    conan_catalog: Catalog,
) -> None:
    dependency = conan_catalog.publish(record())
    _, directory = conan_records.locate(conan_catalog._client, dependency.ref)
    (directory / "conanfile.py").write_text("raise RuntimeError('must not execute')\n")
    with pytest.raises(InvalidPackage, match="canonical"):
        conan_catalog.publish(record("be", package_type="lib-lef"), dependencies=(dependency.ref,))


def test_conan_round_trip_search_and_project_lockfile(
    conan_catalog: Catalog, tmp_path: Path
) -> None:
    rtl, be = record(), record("be", "4.0", package_type="lib-lef")
    edge = Dependency(be.ref, rtl.ref)
    catalog = conan_catalog
    catalog.publish(rtl)
    catalog.publish(be, dependencies=(rtl.ref,))
    stored = Ref(**rtl.ref.to_dict(), catalog=catalog).get()
    assert stored.payload == {"files": ("cpu.rtl",)}
    assert Ref(**be.ref.to_dict(), catalog=catalog).get().ref == be.ref
    for query in (
        SearchQuery(view="rtl"),
        SearchQuery(version="1.0"),
        SearchQuery(component="cpu", view="rtl", namespace="soc"),
    ):
        assert catalog.search(query) == (stored,)
    assert not catalog.search(SearchQuery(namespace="with/slash"))
    assert not catalog.search(SearchQuery(component="missing"))
    assert not catalog.search(SearchQuery(component=""))
    expected = resolve_published(catalog, be.ref)
    assert expected.dependencies == (edge,)
    assert len(expected.to_networkx()) == 2
    assert Ref(**be.ref.to_dict(), catalog=catalog).dependencies()[0] == stored
    assert (
        Ref(**rtl.ref.to_dict(), catalog=catalog)
        .find_dependents(candidates=SearchQuery(), transitive=False)[0]
        .ref
        == be.ref
    )
    assert not Ref(**be.ref.to_dict(), catalog=catalog).dependencies(
        filters=SearchQuery(view="missing")
    )
    _, folder = conan_records.locate(conan_catalog._client, be.ref)
    assert not (folder / "conan.lock").exists()
    lockfile = Graph(catalog, record(name="consumer"), dependencies=(be.ref,)).create_lockfile(
        tmp_path / "workspace/conan.lock"
    )
    assert lockfile.is_file()
    assert resolve_published(catalog, be.ref, lockfile=lockfile) == expected
    catalog.publish(record(version="2.0"))
    assert resolve_published(catalog, be.ref) == expected
    with pytest.raises(Conflict):
        catalog.publish(be)
    with pytest.raises(NotFound):
        Ref(**record(name="missing").ref.to_dict(), catalog=catalog).get()


def test_conan_reverse_search_uses_each_candidates_graph(conan_catalog: Catalog) -> None:
    rtl, other = record(), record(version="2.0")
    mid = record("intermediate", "3.0", "other-component")
    be, wrong, unrelated = (
        record("be", version, package_type="lib-lef") for version in ("4.0", "5.0", "6.0")
    )
    catalog = conan_catalog
    catalog.publish(rtl)
    catalog.publish(other)
    catalog.publish(mid, dependencies=(rtl.ref,))
    catalog.publish(be, dependencies=(mid.ref,))
    catalog.publish(wrong, dependencies=(other.ref,))
    catalog.publish(unrelated)
    query = SearchQuery(namespace="soc", component="cpu", type="lib-lef", view="be")
    assert tuple(
        r.ref for r in Ref(**rtl.ref.to_dict(), catalog=catalog).find_dependents(candidates=query)
    ) == (be.ref,)
    assert not Ref(**rtl.ref.to_dict(), catalog=catalog).find_dependents(
        candidates=query, transitive=False
    )
    assert tuple(
        r.ref for r in Ref(**other.ref.to_dict(), catalog=catalog).find_dependents(candidates=query)
    ) == (wrong.ref,)
    assert not Ref(**rtl.ref.to_dict(), catalog=catalog).find_dependents(
        candidates=SearchQuery(view="rtl")
    )
    incoming = Ref(**rtl.ref.to_dict(), catalog=catalog).find_dependents(
        candidates=SearchQuery(), transitive=False
    )
    assert tuple(n.ref for n in incoming) == (mid.ref,)
    with pytest.raises(NotFound):
        Ref(**record(name="absent").ref.to_dict(), catalog=catalog).find_dependents(
            candidates=query
        )


def test_conan_rejects_conflicting_direct_requirements(conan_catalog: Catalog) -> None:
    root, first, second = record("be", package_type="lib-lef"), record(), record(version="2.0")
    first = conan_catalog.publish(first)
    second = conan_catalog.publish(second)
    with pytest.raises(CatalogError):
        conan_catalog.publish(root, dependencies=(first.ref, second.ref))
    assert (
        Ref(**first.ref.to_dict(), catalog=conan_catalog).get(),
        Ref(**second.ref.to_dict(), catalog=conan_catalog).get(),
    ) == (first, second)


def test_native_conan_diamond_conflict_is_a_domain_error(conan_catalog: Catalog) -> None:
    root = record("be", package_type="lib-lef")
    left, right = record(name="left"), record(name="right")
    first, second = record(), record(version="2.0")
    first = conan_catalog.publish(first)
    second = conan_catalog.publish(second)
    left = conan_catalog.publish(left, dependencies=(first.ref,))
    right = conan_catalog.publish(right, dependencies=(second.ref,))
    with pytest.raises(Conflict):
        conan_catalog.publish(root, dependencies=(left.ref, right.ref))
    assert (
        Ref(**left.ref.to_dict(), catalog=conan_catalog).get(),
        Ref(**right.ref.to_dict(), catalog=conan_catalog).get(),
    ) == (left, right)


def test_external_ci_recipe_is_readable(conan_catalog: Catalog, tmp_path: Path) -> None:
    rtl, be = record(), record("be", "4.0", package_type="lib-lef")
    conan_catalog.publish(rtl)
    build = tmp_path / "external-ci"
    write_recipe(build, be, (to_conan_reference(rtl.ref),))
    cli = conan_catalog._client
    cli.run(
        "lock",
        "create",
        str(build),
        "--lockfile=",
        "--lockfile-out",
        str(build / "conan.lock"),
        "--no-remote",
    )
    cli.run("export", str(build), "--no-remote")
    assert Ref(**be.ref.to_dict(), catalog=conan_catalog).get().ref == be.ref
    assert resolve_published(conan_catalog, be.ref).dependencies == (Dependency(be.ref, rtl.ref),)


@pytest.mark.parametrize("damage", ["missing", "bad-marker", "noncanonical-recipe"])
def test_corrupt_recipe_is_an_error(conan_catalog: Catalog, damage: str) -> None:
    rtl, be = record(), record("be", package_type="lib-lef")
    conan_catalog.publish(rtl)
    conan_catalog.publish(be, dependencies=(rtl.ref,))
    _, folder = conan_records.locate(conan_catalog._client, be.ref)
    if damage == "missing":
        (folder / "record.json").unlink()
    elif damage == "bad-marker":
        (folder / "hwcatalog.json").write_text('{"format":999}')
    else:
        (folder / "conanfile.py").write_text("raise RuntimeError('must not execute')\n")
    with pytest.raises(InvalidPackage):
        resolve_published(conan_catalog, be.ref)


def test_multiple_revisions_are_not_silently_selected(
    conan_catalog: Catalog, tmp_path: Path
) -> None:
    original = record()
    conan_catalog.publish(original)
    folder = tmp_path / "replacement"
    write_recipe(folder, replace(original, payload={"different": True}))
    conan_catalog._client.create_lockfile(folder)
    conan_catalog._client.export(folder)
    with pytest.raises(Conflict):
        Ref(**original.ref.to_dict(), catalog=conan_catalog).get()


def test_upload_failure_is_reported_for_the_single_package(
    conan_catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = record()

    def fail_upload(reference: str) -> None:
        assert reference.startswith("soc.cpu.rtl.default/1.0#")
        raise CatalogError("simulated lost response")

    monkeypatch.setattr(conan_catalog._client, "upload", fail_upload)
    with pytest.raises(CatalogError, match="simulated lost response"):
        conan_catalog.publish(root)


def test_command_os_error_is_not_not_found(
    conan_catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hwcatalog._internal.conan import client

    def fail(*args: object, **kwargs: object) -> object:
        raise OSError("API initialization failed")

    monkeypatch.setattr(client, "ConanAPI", fail)
    with pytest.raises(CatalogError, match="API initialization failed"):
        Ref(**record().ref.to_dict(), catalog=conan_catalog).get()


def test_native_graph_exposes_conans_dependencies_and_topological_order(
    conan_catalog: Catalog,
) -> None:
    # Conan's native runtime class has no typing metadata.
    from conan.internal.graph.graph import DepsGraph  # type: ignore[import-untyped]

    root = record(name="aaa-root")
    left, right = record(name="aaa-left"), record(name="bbb-right")
    shared = record(name="zzz-shared")
    conan_catalog.publish(shared)
    conan_catalog.publish(left, dependencies=(shared.ref,))
    conan_catalog.publish(right, dependencies=(shared.ref,))
    conan_catalog.publish(root, dependencies=(left.ref, right.ref))
    graph, _ = conan_graph.load_graph(conan_catalog._client, root.ref)
    assert isinstance(graph, DepsGraph)
    assert len(graph.nodes) == 4
    assert "nodes" in graph.serialize()
    dependencies = graph.root.conanfile.dependencies
    direct = {
        from_conan_reference(dep.ref.repr_notime()) for dep in dependencies.direct_host.values()
    }
    assert direct == {left.ref, right.ref}
    ordered = tuple(
        from_conan_reference(dep.ref.repr_notime())
        for dep in dependencies.host.topological_sort.values()
    )
    assert set(ordered) == {left.ref, right.ref, shared.ref}
    assert ordered.index(shared.ref) < ordered.index(left.ref)
    assert ordered.index(shared.ref) < ordered.index(right.ref)
    assert (
        dependencies.host[to_conan_reference(shared.ref)].ref.name == "soc.zzz-shared.rtl.default"
    )
    edges = (
        Dependency(root.ref, left.ref),
        Dependency(root.ref, right.ref),
        Dependency(left.ref, shared.ref),
        Dependency(right.ref, shared.ref),
    )
    assert resolve_published(conan_catalog, root.ref).dependencies == tuple(
        sorted(edges, key=lambda edge: (edge.source, edge.target))
    )


def test_native_graph_uses_explicit_home_without_changing_process_state(
    conan_catalog: Catalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = conan_catalog.publish(record())
    ambient_home = tmp_path / "unrelated-home"
    monkeypatch.setenv("CONAN_HOME", str(ambient_home))
    cwd = Path.cwd()
    graph, _ = conan_graph.load_graph(conan_catalog._client, root.ref)
    assert tuple(graph.root.conanfile.dependencies.host.topological_sort.values()) == ()
    assert graph.root.conanfile.recipe_folder is not None
    assert Path(graph.root.conanfile.recipe_folder).is_relative_to(conan_catalog._client.home)
    assert os.environ["CONAN_HOME"] == str(ambient_home)
    assert Path.cwd() == cwd
    assert not ambient_home.exists()
    assert resolve_published(conan_catalog, root.ref).records == (root,)


def test_native_graph_reports_missing_pinned_dependency(conan_catalog: Catalog) -> None:
    rtl, macro = record(), record("be", package_type="lib-lef")
    conan_catalog.publish(rtl)
    conan_catalog.publish(macro, dependencies=(rtl.ref,))
    conan_catalog._client.run("remove", to_conan_reference(rtl.ref), "--confirm")
    with pytest.raises(CatalogError):
        conan_graph.load_graph(conan_catalog._client, macro.ref)


def test_native_api_failure_is_a_catalog_error(
    conan_catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from conan.errors import ConanException  # type: ignore[import-untyped]

    from hwcatalog._internal.conan import client

    root = record()
    conan_catalog.publish(root)

    def fail(*args: object, **kwargs: object) -> object:
        raise ConanException("simulated API failure")

    monkeypatch.setattr(client, "ConanAPI", fail)
    with pytest.raises(CatalogError, match="simulated API failure"):
        conan_graph.load_graph(conan_catalog._client, root.ref)


def test_native_graph_computation_does_not_spawn_a_conan_process(
    conan_catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_subprocess(*args: object, **kwargs: object) -> object:
        raise AssertionError("Catalog operations must use the in-process Conan API")

    monkeypatch.setattr(subprocess, "run", reject_subprocess)
    monkeypatch.setattr(subprocess, "Popen", reject_subprocess)
    root = record()
    conan_catalog.publish(root)
    assert Ref(**root.ref.to_dict(), catalog=conan_catalog).get().ref == root.ref
    assert conan_catalog.search(SearchQuery())[0].ref == root.ref
    assert len(resolve_published(conan_catalog, root.ref).records) == 1


def test_native_graph_rejects_an_unlocked_transitive_dependency(
    conan_catalog: Catalog, tmp_path: Path
) -> None:
    root, middle, leaf = (record(name=name) for name in ("root", "middle", "leaf"))
    conan_catalog.publish(leaf)
    conan_catalog.publish(middle, dependencies=(leaf.ref,))
    conan_catalog.publish(root, dependencies=(middle.ref,))
    lock_path = Graph(
        conan_catalog, record(name="consumer"), dependencies=(root.ref,)
    ).create_lockfile(tmp_path / "project/conan.lock")
    conan_catalog._client.run(
        "lock",
        "remove",
        "--requires=soc.leaf.rtl.default/*",
        "--lockfile",
        str(lock_path),
        "--lockfile-out",
        str(lock_path),
    )
    with pytest.raises(InvalidPackage):
        conan_graph.load_graph(conan_catalog._client, root.ref, lockfile=lock_path)


def test_default_cache_uses_conan_home_and_stays_bound_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    home = configured_home(tmp_path / "native-home")
    monkeypatch.setenv("CONAN_HOME", str(home))
    connection = Catalog()
    assert connection._client.home == home.resolve()
    other = tmp_path / "later-home"
    monkeypatch.setenv("CONAN_HOME", str(other))
    catalog = connection
    catalog.publish(record())
    assert Ref(**record().ref.to_dict(), catalog=catalog).get().ref == record().ref
    assert not other.exists()


def test_default_cache_uses_native_conanrc_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CONAN_HOME", raising=False)
    configured_home(tmp_path / "configured-home")
    (tmp_path / ".conanrc").write_text("conan_home=./configured-home\n")
    connection = Catalog()
    assert connection._client.home == (tmp_path / "configured-home").resolve()


def test_invalid_default_cache_is_a_domain_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONAN_HOME", "relative-path")
    with pytest.raises(CatalogError, match="default home"):
        Catalog()
