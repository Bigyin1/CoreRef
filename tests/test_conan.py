"""Behavioral tests against real Conan recipes and its dependency graph evaluator."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from coreref._internal.recipe import LocalRecipe
from coreref._internal.references import (
    from_conan_reference,
    to_conan_reference,
)
from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError, Conflict, InvalidPackage, NotFound
from coreref.api.graph import Graph
from coreref.api.query import SearchQuery
from coreref.api.records import Record, Ref
from coreref.testing import configured_home
from tests.helpers import local_record, resolve_published

pytestmark = pytest.mark.conan


@pytest.fixture
def conan_catalog(catalog: Catalog) -> Catalog:
    return catalog


def record(
    kind: str = "rtl", version: str = "1.0", name: str = "cpu", *, package_type: str = "default"
) -> Record:
    return Record(
        Ref.from_fields("soc", name, kind, package_type, version),
        {"files": [f"{name}.{kind}"]},
    )


@pytest.mark.parametrize("field", ["namespace", "component", "view", "type", "version"])
@pytest.mark.parametrize("value", ["with.dot", "with/slash", "Upper", "", "*", "a b"])
def test_conan_coordinates_reject_lossy_conversion(field: str, value: str) -> None:
    if field == "version" and value == "with.dot":
        return
    with pytest.raises(InvalidPackage):
        coordinates = record().ref.to_dict()
        coordinates[field] = value
        to_conan_reference(Ref.from_fields(**coordinates))


def test_reference_round_trip() -> None:
    ref = record("be", "4.0", package_type="lib-lef").ref
    assert to_conan_reference(ref) == "soc.cpu.be.lib-lef/4.0"
    assert from_conan_reference("soc.cpu.be.lib-lef/4.0#" + "a" * 32).without_revision() == ref
    with pytest.raises(InvalidPackage):
        from_conan_reference("ordinary-library/1.0@user/channel")
    with pytest.raises(InvalidPackage):
        to_conan_reference(Ref.from_fields(**(ref.to_dict() | {"component": "a" * 101})))
    with pytest.raises(InvalidPackage):
        to_conan_reference(Ref.from_fields(**(ref.to_dict() | {"namespace": "-invalid"})))


def test_publish_uses_one_export_pkg_without_graph_or_export_commands(
    conan_catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    operations: list[tuple[str, ...]] = []
    original_run = conan_catalog._client.run

    def track(*arguments: str) -> object:
        operations.append(arguments)
        return original_run(*arguments)

    monkeypatch.setattr(conan_catalog._client, "run", track)
    published = conan_catalog.publish(local_record(record()))
    assert published.ref.without_revision() == record().ref
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
    published = conan_catalog.publish(local_record(record()))
    assert conan_catalog.get(Ref.from_fields(**published.ref.to_dict())) == published


def test_publish_rejects_invalid_resolved_metadata_before_exporting(
    conan_catalog: Catalog,
) -> None:
    dependency = conan_catalog.publish(local_record(record()))
    directory = conan_catalog._package(dependency.ref).recipe().directory
    (directory / "record.json").write_text('{"format": -1}')
    with pytest.raises(InvalidPackage, match="Unsupported record format"):
        conan_catalog.publish(
            local_record(record("be", package_type="lib-lef")),
            dependencies=(dependency.ref,),
        )


def test_conan_round_trip_search_and_project_lockfile(
    conan_catalog: Catalog, tmp_path: Path
) -> None:
    rtl, be = (
        record(),
        record("be", "4.0", package_type="lib-lef"),
    )
    catalog = conan_catalog
    rtl = catalog.publish(local_record(rtl))
    be = catalog.publish(local_record(be), dependencies=(rtl.ref,))
    stored = catalog.get(Ref.from_fields(**rtl.ref.to_dict()))
    assert stored.payload == {"files": ("cpu.rtl",)}
    assert catalog.get(Ref.from_fields(**be.ref.to_dict())).ref == be.ref
    for query in (
        SearchQuery(view="rtl"),
        SearchQuery(version="1.0"),
        SearchQuery(component="cpu", view="rtl", namespace="soc"),
    ):
        assert catalog.search(query) == (stored,)
    with pytest.raises(InvalidPackage):
        SearchQuery(namespace="with/slash")
    assert not catalog.search(SearchQuery(component="missing"))
    with pytest.raises(InvalidPackage):
        SearchQuery(component="")
    expected = resolve_published(catalog, be.ref)
    assert tuple(sorted(expected.networkx.edges)) == ((be.ref, rtl.ref),)
    assert len(expected.networkx) == 2
    assert Graph(catalog, Ref.from_fields(**be.ref.to_dict())).dependencies()[0] == stored
    assert not Graph(catalog, Ref.from_fields(**be.ref.to_dict())).dependencies(
        filters=SearchQuery(view="missing")
    )
    folder = conan_catalog._package(be.ref).recipe().directory
    assert not (folder / "conan.lock").exists()
    lockfile = Graph(
        conan_catalog,
        local_record(record(name="consumer")),
        dependencies=(be.ref,),
    ).create_lockfile(tmp_path / "workspace/conan.lock")
    assert lockfile.is_file()
    assert resolve_published(catalog, be.ref, lockfile=lockfile) == expected
    catalog.publish(local_record(record(version="2.0")))
    assert resolve_published(catalog, be.ref) == expected
    assert catalog.publish(local_record(be), dependencies=(rtl.ref,)) == be
    with pytest.raises(NotFound):
        catalog.get(Ref.from_fields(**record(name="missing").ref.to_dict()))


def test_conan_rejects_conflicting_direct_requirements(conan_catalog: Catalog) -> None:
    root, first, second = (
        record("be", package_type="lib-lef"),
        record(),
        record(version="2.0"),
    )
    first = conan_catalog.publish(local_record(first))
    second = conan_catalog.publish(local_record(second))
    with pytest.raises(CatalogError):
        root = conan_catalog.publish(local_record(root), dependencies=(first.ref, second.ref))
    assert (
        conan_catalog.get(Ref.from_fields(**first.ref.to_dict())),
        conan_catalog.get(Ref.from_fields(**second.ref.to_dict())),
    ) == (first, second)


def test_native_conan_diamond_conflict_is_a_domain_error(conan_catalog: Catalog) -> None:
    root = record("be", package_type="lib-lef")
    left, right = (
        record(name="left"),
        record(name="right"),
    )
    first, second = record(), record(version="2.0")
    first = conan_catalog.publish(local_record(first))
    second = conan_catalog.publish(local_record(second))
    left = conan_catalog.publish(local_record(left), dependencies=(first.ref,))
    right = conan_catalog.publish(local_record(right), dependencies=(second.ref,))
    with pytest.raises(Conflict):
        root = conan_catalog.publish(local_record(root), dependencies=(left.ref, right.ref))
    assert (
        conan_catalog.get(Ref.from_fields(**left.ref.to_dict())),
        conan_catalog.get(Ref.from_fields(**right.ref.to_dict())),
    ) == (left, right)


def test_external_ci_recipe_is_readable(conan_catalog: Catalog, tmp_path: Path) -> None:
    rtl, be = (
        record(),
        record("be", "4.0", package_type="lib-lef"),
    )
    rtl = conan_catalog.publish(local_record(rtl))
    build = tmp_path / "external-ci"
    LocalRecipe(local_record(be), (to_conan_reference(rtl.ref),)).write(build)
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
    cli.upload(cli.export(build))
    assert conan_catalog.get(Ref.from_fields(**be.ref.to_dict())).ref.without_revision() == be.ref
    graph = Graph(conan_catalog, be.ref)
    assert tuple(sorted(graph.networkx.edges)) == ((graph.record.ref, rtl.ref),)


@pytest.mark.parametrize("damage", ["missing", "missing-data", "bad-marker", "bad-coordinates"])
def test_corrupt_recipe_is_an_error(conan_catalog: Catalog, damage: str) -> None:
    rtl, be = (
        record(),
        record("be", package_type="lib-lef"),
    )
    rtl = conan_catalog.publish(local_record(rtl))
    be = conan_catalog.publish(local_record(be), dependencies=(rtl.ref,))
    folder = conan_catalog._package(be.ref).recipe().directory
    if damage == "missing":
        (folder / "record.json").unlink()
    elif damage == "missing-data":
        (folder / "conandata.yml").unlink()
    elif damage == "bad-marker":
        data = json.loads((folder / "record.json").read_text())
        data["format"] = 999
        (folder / "record.json").write_text(json.dumps(data))
    else:
        data = json.loads((folder / "record.json").read_text())
        data["ref"]["component"] = "different"
        (folder / "record.json").write_text(json.dumps(data))
    with pytest.raises(InvalidPackage):
        conan_catalog.info(be.ref)
    with pytest.raises(CatalogError):
        Graph(conan_catalog, be.ref)


def test_latest_and_explicit_revisions_are_independently_accessible(
    conan_catalog: Catalog, tmp_path: Path
) -> None:
    original = record()
    original = conan_catalog.publish(local_record(original))
    folder = tmp_path / "replacement"
    LocalRecipe(local_record(Record(original.ref, {"different": True}))).write(folder)
    conan_catalog._client.create_lockfile(folder)
    conan_catalog._client.upload(conan_catalog._client.export(folder))
    assert conan_catalog.get(original.ref).ref == original.ref
    assert conan_catalog.get(original.ref.without_revision()).payload == {"different": True}


def test_upload_failure_is_reported_for_the_single_package(
    conan_catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = record()

    def fail_upload(reference: str) -> None:
        assert reference.startswith("soc.cpu.rtl.default/1.0#")
        raise CatalogError("simulated lost response")

    monkeypatch.setattr(conan_catalog._client, "upload", fail_upload)
    with pytest.raises(CatalogError, match="simulated lost response"):
        root = conan_catalog.publish(local_record(root))


def test_command_os_error_is_not_not_found(
    conan_catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coreref._internal import client

    def fail(*_args: object, **_kwargs: object) -> object:
        raise OSError("API initialization failed")

    monkeypatch.setattr(client, "ConanAPI", fail)
    with pytest.raises(CatalogError, match="Could not inspect working packages"):
        conan_catalog.get(Ref.from_fields(**record().ref.to_dict()))


def test_native_graph_exposes_conans_dependencies_and_topological_order(
    conan_catalog: Catalog,
) -> None:
    # Conan's native runtime class has no typing metadata.
    from conan.internal.graph.graph import DepsGraph  # type: ignore[import-untyped]

    root = record(name="aaa-root")
    left, right = (
        record(name="aaa-left"),
        record(name="bbb-right"),
    )
    shared = record(name="zzz-shared")
    shared = conan_catalog.publish(local_record(shared))
    left = conan_catalog.publish(local_record(left), dependencies=(shared.ref,))
    right = conan_catalog.publish(local_record(right), dependencies=(shared.ref,))
    root = conan_catalog.publish(local_record(root), dependencies=(left.ref, right.ref))
    graph = conan_catalog._client.graph(conan_catalog._package(root.ref).recipe().directory)
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
        (root.ref, left.ref),
        (root.ref, right.ref),
        (left.ref, shared.ref),
        (right.ref, shared.ref),
    )
    assert tuple(sorted(resolve_published(conan_catalog, root.ref).networkx.edges)) == tuple(
        sorted(edges, key=lambda edge: (edge[0], edge[1]))
    )


def test_native_graph_uses_explicit_home_without_changing_process_state(
    conan_catalog: Catalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = conan_catalog.publish(local_record(record()))
    ambient_home = tmp_path / "unrelated-home"
    monkeypatch.setenv("CONAN_HOME", str(ambient_home))
    cwd = Path.cwd()
    graph = conan_catalog._client.graph(conan_catalog._package(root.ref).recipe().directory)
    assert tuple(graph.root.conanfile.dependencies.host.topological_sort.values()) == ()
    assert graph.root.conanfile.recipe_folder is not None
    assert Path(graph.root.conanfile.recipe_folder).is_relative_to(conan_catalog._client.home)
    assert os.environ["CONAN_HOME"] == str(ambient_home)
    assert Path.cwd() == cwd
    assert not ambient_home.exists()
    assert resolve_published(conan_catalog, root.ref).records == (root,)


def test_native_graph_reports_missing_pinned_dependency(conan_catalog: Catalog) -> None:
    rtl, macro = (
        record(),
        record("be", package_type="lib-lef"),
    )
    rtl = conan_catalog.publish(local_record(rtl))
    macro = conan_catalog.publish(local_record(macro), dependencies=(rtl.ref,))
    conan_catalog._client.run(
        "remove", to_conan_reference(rtl.ref), "--remote", "catalog-test", "--confirm"
    )
    conan_catalog._client.run("remove", to_conan_reference(rtl.ref), "--confirm")
    with pytest.raises(CatalogError):
        conan_catalog._client.graph(conan_catalog._package(macro.ref).recipe().directory)


def test_native_api_failure_is_a_catalog_error(
    conan_catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from conan.errors import ConanException  # type: ignore[import-untyped]

    from coreref._internal import client

    root = record()
    root = conan_catalog.publish(local_record(root))

    def fail(*_args: object, **_kwargs: object) -> object:
        raise ConanException("simulated API failure")

    monkeypatch.setattr(client, "ConanAPI", fail)
    with pytest.raises(CatalogError, match="Could not resolve dependencies"):
        resolve_published(conan_catalog, root.ref)


def test_native_graph_computation_does_not_spawn_a_conan_process(
    conan_catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_subprocess(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Catalog operations must use the in-process Conan API")

    monkeypatch.setattr(subprocess, "run", reject_subprocess)
    monkeypatch.setattr(subprocess, "Popen", reject_subprocess)
    root = record()
    root = conan_catalog.publish(local_record(root))
    assert conan_catalog.get(Ref.from_fields(**root.ref.to_dict())).ref == root.ref
    assert conan_catalog.search(SearchQuery())[0].ref == root.ref
    assert len(resolve_published(conan_catalog, root.ref).records) == 1


def test_native_graph_rejects_an_unlocked_transitive_dependency(
    conan_catalog: Catalog, tmp_path: Path
) -> None:
    root, middle, leaf = (record(name=name) for name in ("root", "middle", "leaf"))
    leaf = conan_catalog.publish(local_record(leaf))
    middle = conan_catalog.publish(local_record(middle), dependencies=(leaf.ref,))
    root = conan_catalog.publish(local_record(root), dependencies=(middle.ref,))
    lock_path = Graph(
        conan_catalog,
        local_record(record(name="consumer")),
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
        resolve_published(conan_catalog, root.ref, lockfile=lock_path)


def test_default_cache_uses_conan_home_and_stays_bound_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, catalog: Catalog
) -> None:
    monkeypatch.chdir(tmp_path)
    home = catalog._client.home
    monkeypatch.setenv("CONAN_HOME", str(home))
    connection = Catalog()
    assert connection._client.home == home.resolve()
    other = tmp_path / "later-home"
    monkeypatch.setenv("CONAN_HOME", str(other))
    catalog = connection
    catalog.publish(local_record(record()))
    assert (
        catalog.get(Ref.from_fields(**record().ref.to_dict())).ref.without_revision()
        == record().ref
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
    with pytest.raises(CatalogError, match="client home"):
        Catalog()
