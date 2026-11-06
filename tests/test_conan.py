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
from tests.helpers import local_record, resolve_published

pytestmark = pytest.mark.conan


@pytest.fixture
def conan_catalog(tmp_path: Path) -> Catalog:
    return Catalog(configured_home(tmp_path / "home"))


def record(
    kind: str = "rtl",
    version: str = "1.0",
    name: str = "cpu",
    *,
    package_type: str = "default",
    catalog: Catalog,
) -> Record:
    return Record(
        Ref("soc", name, kind, package_type, version, catalog=catalog),
        {"files": [f"{name}.{kind}"]},
    )


@pytest.mark.parametrize("field", ["namespace", "component", "view", "type", "version"])
@pytest.mark.parametrize("value", ["with.dot", "with/slash", "Upper", "", "*", "a b"])
def test_conan_coordinates_reject_lossy_conversion(
    field: str, value: str, *, catalog: Catalog
) -> None:
    if field == "version" and value == "with.dot":
        return
    with pytest.raises(InvalidPackage):
        coordinates = record(catalog=catalog).ref.to_dict()
        coordinates[field] = value
        to_conan_reference(Ref(**coordinates, catalog=catalog))


def test_reference_round_trip(*, catalog: Catalog) -> None:
    ref = record("be", "4.0", package_type="lib-lef", catalog=catalog).ref
    assert to_conan_reference(ref) == "soc.cpu.be.lib-lef/4.0"
    assert from_conan_reference("soc.cpu.be.lib-lef/4.0#" + "a" * 32, catalog=catalog) == ref
    with pytest.raises(InvalidPackage):
        from_conan_reference("ordinary-library/1.0@user/channel", catalog=catalog)
    with pytest.raises(InvalidPackage):
        to_conan_reference(replace(ref, component="a" * 101, catalog=catalog))
    with pytest.raises(InvalidPackage):
        to_conan_reference(replace(ref, namespace="-invalid", catalog=catalog))


def test_publish_uses_one_export_pkg_without_graph_or_export_commands(
    conan_catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    operations: list[tuple[str, ...]] = []
    original_run = conan_catalog._client.run

    def track(*arguments: str) -> object:
        operations.append(arguments)
        return original_run(*arguments)

    monkeypatch.setattr(conan_catalog._client, "run", track)
    published = conan_catalog.publish(local_record(record(catalog=conan_catalog)))
    assert published.ref == record(catalog=conan_catalog).ref
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
    published = conan_catalog.publish(local_record(record(catalog=conan_catalog)))
    assert Ref(**published.ref.to_dict(), catalog=conan_catalog).get() == published


def test_publish_rejects_an_invalid_catalog_dependency_before_conan_executes_it(
    conan_catalog: Catalog,
) -> None:
    dependency = conan_catalog.publish(local_record(record(catalog=conan_catalog)))
    _, directory = conan_records.locate(conan_catalog._client, dependency.ref)
    (directory / "conanfile.py").write_text("raise RuntimeError('must not execute')\n")
    with pytest.raises(InvalidPackage, match="canonical"):
        conan_catalog.publish(
            local_record(record("be", package_type="lib-lef", catalog=conan_catalog)),
            dependencies=(dependency.ref,),
        )


def test_conan_round_trip_search_and_project_lockfile(
    conan_catalog: Catalog, tmp_path: Path
) -> None:
    rtl, be = (
        record(catalog=conan_catalog),
        record("be", "4.0", package_type="lib-lef", catalog=conan_catalog),
    )
    edge = Dependency(be.ref, rtl.ref)
    catalog = conan_catalog
    catalog.publish(local_record(rtl))
    catalog.publish(local_record(be), dependencies=(rtl.ref,))
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
    lockfile = Graph(
        catalog,
        local_record(record(name="consumer", catalog=conan_catalog)),
        dependencies=(be.ref,),
    ).create_lockfile(tmp_path / "workspace/conan.lock")
    assert lockfile.is_file()
    assert resolve_published(catalog, be.ref, lockfile=lockfile) == expected
    catalog.publish(local_record(record(version="2.0", catalog=conan_catalog)))
    assert resolve_published(catalog, be.ref) == expected
    with pytest.raises(Conflict):
        catalog.publish(local_record(be))
    with pytest.raises(NotFound):
        Ref(**record(name="missing", catalog=conan_catalog).ref.to_dict(), catalog=catalog).get()


def test_conan_reverse_search_uses_each_candidates_graph(conan_catalog: Catalog) -> None:
    rtl, other = record(catalog=conan_catalog), record(version="2.0", catalog=conan_catalog)
    mid = record("intermediate", "3.0", "other-component", catalog=conan_catalog)
    be, wrong, unrelated = (
        record("be", version, package_type="lib-lef", catalog=conan_catalog)
        for version in ("4.0", "5.0", "6.0")
    )
    catalog = conan_catalog
    catalog.publish(local_record(rtl))
    catalog.publish(local_record(other))
    catalog.publish(local_record(mid), dependencies=(rtl.ref,))
    catalog.publish(local_record(be), dependencies=(mid.ref,))
    catalog.publish(local_record(wrong), dependencies=(other.ref,))
    catalog.publish(local_record(unrelated))
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
        Ref(
            **record(name="absent", catalog=conan_catalog).ref.to_dict(), catalog=catalog
        ).find_dependents(candidates=query)


def test_conan_rejects_conflicting_direct_requirements(conan_catalog: Catalog) -> None:
    root, first, second = (
        record("be", package_type="lib-lef", catalog=conan_catalog),
        record(catalog=conan_catalog),
        record(version="2.0", catalog=conan_catalog),
    )
    first = conan_catalog.publish(local_record(first))
    second = conan_catalog.publish(local_record(second))
    with pytest.raises(CatalogError):
        conan_catalog.publish(local_record(root), dependencies=(first.ref, second.ref))
    assert (
        Ref(**first.ref.to_dict(), catalog=conan_catalog).get(),
        Ref(**second.ref.to_dict(), catalog=conan_catalog).get(),
    ) == (first, second)


def test_native_conan_diamond_conflict_is_a_domain_error(conan_catalog: Catalog) -> None:
    root = record("be", package_type="lib-lef", catalog=conan_catalog)
    left, right = (
        record(name="left", catalog=conan_catalog),
        record(name="right", catalog=conan_catalog),
    )
    first, second = record(catalog=conan_catalog), record(version="2.0", catalog=conan_catalog)
    first = conan_catalog.publish(local_record(first))
    second = conan_catalog.publish(local_record(second))
    left = conan_catalog.publish(local_record(left), dependencies=(first.ref,))
    right = conan_catalog.publish(local_record(right), dependencies=(second.ref,))
    with pytest.raises(Conflict):
        conan_catalog.publish(local_record(root), dependencies=(left.ref, right.ref))
    assert (
        Ref(**left.ref.to_dict(), catalog=conan_catalog).get(),
        Ref(**right.ref.to_dict(), catalog=conan_catalog).get(),
    ) == (left, right)


def test_external_ci_recipe_is_readable(conan_catalog: Catalog, tmp_path: Path) -> None:
    rtl, be = (
        record(catalog=conan_catalog),
        record("be", "4.0", package_type="lib-lef", catalog=conan_catalog),
    )
    conan_catalog.publish(local_record(rtl))
    build = tmp_path / "external-ci"
    write_recipe(build, local_record(be), (to_conan_reference(rtl.ref),))
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
    rtl, be = (
        record(catalog=conan_catalog),
        record("be", package_type="lib-lef", catalog=conan_catalog),
    )
    conan_catalog.publish(local_record(rtl))
    conan_catalog.publish(local_record(be), dependencies=(rtl.ref,))
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
    original = record(catalog=conan_catalog)
    conan_catalog.publish(local_record(original))
    folder = tmp_path / "replacement"
    write_recipe(folder, local_record(replace(original, payload={"different": True})))
    conan_catalog._client.create_lockfile(folder)
    conan_catalog._client.export(folder)
    with pytest.raises(Conflict):
        Ref(**original.ref.to_dict(), catalog=conan_catalog).get()


def test_upload_failure_is_reported_for_the_single_package(
    conan_catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = record(catalog=conan_catalog)

    def fail_upload(reference: str) -> None:
        assert reference.startswith("soc.cpu.rtl.default/1.0#")
        raise CatalogError("simulated lost response")

    monkeypatch.setattr(conan_catalog._client, "upload", fail_upload)
    with pytest.raises(CatalogError, match="simulated lost response"):
        conan_catalog.publish(local_record(root))


def test_command_os_error_is_not_not_found(
    conan_catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hwcatalog._internal.conan import client

    def fail(*_args: object, **_kwargs: object) -> object:
        raise OSError("API initialization failed")

    monkeypatch.setattr(client, "ConanAPI", fail)
    with pytest.raises(CatalogError, match="API initialization failed"):
        Ref(**record(catalog=conan_catalog).ref.to_dict(), catalog=conan_catalog).get()


def test_native_graph_exposes_conans_dependencies_and_topological_order(
    conan_catalog: Catalog,
) -> None:
    # Conan's native runtime class has no typing metadata.
    from conan.internal.graph.graph import DepsGraph  # type: ignore[import-untyped]

    root = record(name="aaa-root", catalog=conan_catalog)
    left, right = (
        record(name="aaa-left", catalog=conan_catalog),
        record(name="bbb-right", catalog=conan_catalog),
    )
    shared = record(name="zzz-shared", catalog=conan_catalog)
    conan_catalog.publish(local_record(shared))
    conan_catalog.publish(local_record(left), dependencies=(shared.ref,))
    conan_catalog.publish(local_record(right), dependencies=(shared.ref,))
    conan_catalog.publish(local_record(root), dependencies=(left.ref, right.ref))
    graph, _ = conan_graph.load_graph(conan_catalog._client, root.ref)
    assert isinstance(graph, DepsGraph)
    assert len(graph.nodes) == 4
    assert "nodes" in graph.serialize()
    dependencies = graph.root.conanfile.dependencies
    direct = {
        from_conan_reference(dep.ref.repr_notime(), catalog=conan_catalog)
        for dep in dependencies.direct_host.values()
    }
    assert direct == {left.ref, right.ref}
    ordered = tuple(
        from_conan_reference(dep.ref.repr_notime(), catalog=conan_catalog)
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
    root = conan_catalog.publish(local_record(record(catalog=conan_catalog)))
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
    assert resolve_published(conan_catalog, root.ref).records == (local_record(root),)


def test_native_graph_reports_missing_pinned_dependency(conan_catalog: Catalog) -> None:
    rtl, macro = (
        record(catalog=conan_catalog),
        record("be", package_type="lib-lef", catalog=conan_catalog),
    )
    conan_catalog.publish(local_record(rtl))
    conan_catalog.publish(local_record(macro), dependencies=(rtl.ref,))
    conan_catalog._client.run("remove", to_conan_reference(rtl.ref), "--confirm")
    with pytest.raises(CatalogError):
        conan_graph.load_graph(conan_catalog._client, macro.ref)


def test_native_api_failure_is_a_catalog_error(
    conan_catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from conan.errors import ConanException  # type: ignore[import-untyped]

    from hwcatalog._internal.conan import client

    root = record(catalog=conan_catalog)
    conan_catalog.publish(local_record(root))

    def fail(*_args: object, **_kwargs: object) -> object:
        raise ConanException("simulated API failure")

    monkeypatch.setattr(client, "ConanAPI", fail)
    with pytest.raises(CatalogError, match="simulated API failure"):
        conan_graph.load_graph(conan_catalog._client, root.ref)


def test_native_graph_computation_does_not_spawn_a_conan_process(
    conan_catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_subprocess(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Catalog operations must use the in-process Conan API")

    monkeypatch.setattr(subprocess, "run", reject_subprocess)
    monkeypatch.setattr(subprocess, "Popen", reject_subprocess)
    root = record(catalog=conan_catalog)
    conan_catalog.publish(local_record(root))
    assert Ref(**root.ref.to_dict(), catalog=conan_catalog).get().ref == root.ref
    assert conan_catalog.search(SearchQuery())[0].ref == root.ref
    assert len(resolve_published(conan_catalog, root.ref).records) == 1


def test_native_graph_rejects_an_unlocked_transitive_dependency(
    conan_catalog: Catalog, tmp_path: Path
) -> None:
    root, middle, leaf = (
        record(name=name, catalog=conan_catalog) for name in ("root", "middle", "leaf")
    )
    conan_catalog.publish(local_record(leaf))
    conan_catalog.publish(local_record(middle), dependencies=(leaf.ref,))
    conan_catalog.publish(local_record(root), dependencies=(middle.ref,))
    lock_path = Graph(
        conan_catalog,
        local_record(record(name="consumer", catalog=conan_catalog)),
        dependencies=(root.ref,),
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, catalog: Catalog
) -> None:
    monkeypatch.chdir(tmp_path)
    home = configured_home(tmp_path / "native-home")
    monkeypatch.setenv("CONAN_HOME", str(home))
    connection = Catalog()
    assert connection._client.home == home.resolve()
    other = tmp_path / "later-home"
    monkeypatch.setenv("CONAN_HOME", str(other))
    catalog = connection
    catalog.publish(local_record(record(catalog=catalog)))
    assert (
        Ref(**record(catalog=catalog).ref.to_dict(), catalog=catalog).get().ref
        == record(catalog=catalog).ref
    )
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
