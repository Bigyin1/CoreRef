"""Behavioral tests against real Conan recipes and its dependency graph evaluator."""

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from hwcatalog._internal.backends.conan_format import (
    from_conan_reference,
    to_conan_reference,
    write_recipe,
)
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import (
    BackendUnavailable,
    CatalogError,
    IdentityConflict,
    InvalidPackage,
    InvalidPublication,
    NotFound,
    VersionConflict,
)
from hwcatalog.api.models.graph import Dependency
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.backends.conan import ConanBackend
from tests.helpers import resolve_published

pytestmark = pytest.mark.conan


@pytest.fixture
def conan_backend(tmp_path: Path) -> ConanBackend:
    return ConanBackend(tmp_path / "home")


def record(kind: str = "rtl", version: str = "1.0", name: str = "cpu") -> Record:
    return Record(Ref(name, version, kind, "soc"), {"files": [f"{name}.{kind}"]})


@pytest.mark.parametrize("field", ["namespace", "name", "kind", "version"])
@pytest.mark.parametrize("value", ["with.dot", "with/slash", "Upper", "", "*", "a b"])
def test_conan_coordinates_reject_lossy_conversion(field: str, value: str) -> None:
    if field == "version" and value == "with.dot":
        return
    with pytest.raises(InvalidPublication):
        to_conan_reference(replace(record().ref, **{field: value}))


def test_reference_round_trip() -> None:
    ref = record("lib-lef", "4.0").ref
    assert to_conan_reference(ref) == "soc.cpu.lib-lef/4.0"
    assert from_conan_reference("soc.cpu.lib-lef/4.0#" + "a" * 32) == ref
    with pytest.raises(InvalidPackage):
        from_conan_reference("ordinary-library/1.0@user/channel")
    with pytest.raises(InvalidPublication):
        to_conan_reference(replace(ref, name="a" * 101))
    with pytest.raises(InvalidPublication):
        to_conan_reference(replace(ref, namespace="-invalid"))


def test_publish_uses_one_export_pkg_without_graph_or_export_commands(
    conan_backend: ConanBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    operations: list[tuple[str, ...]] = []
    original_run = conan_backend._client.run

    def track(*arguments: str) -> object:
        operations.append(arguments)
        return original_run(*arguments)

    monkeypatch.setattr(conan_backend._client, "run", track)
    published = conan_backend.publish(record())
    assert published.ref == record().ref
    assert sum(arguments[0] == "export-pkg" for arguments in operations) == 1
    assert not any(arguments[0] == "export" for arguments in operations)
    assert not any(arguments[:2] == ("graph", "info") for arguments in operations)


def test_publish_does_not_scan_unrelated_packages(
    conan_backend: ConanBackend, tmp_path: Path
) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "conanfile.py").write_text(
        "from conan import ConanFile\nclass Foreign(ConanFile):\n"
        '    name = "external.vendor.library.helper"\n    version = "1.0"\n'
    )
    conan_backend._client.export(foreign)
    published = conan_backend.publish(record())
    assert conan_backend.get_many((published.ref,)) == (published,)


def test_publish_rejects_an_invalid_catalog_dependency_before_conan_executes_it(
    conan_backend: ConanBackend,
) -> None:
    dependency = conan_backend.publish(record())
    _, directory = conan_backend._locate(dependency.ref)
    (directory / "conanfile.py").write_text("raise RuntimeError('must not execute')\n")
    with pytest.raises(InvalidPackage, match="canonical"):
        conan_backend.publish(record("lib-lef"), dependencies=(dependency.ref,))


def test_conan_round_trip_search_and_project_lockfile(
    conan_backend: ConanBackend, tmp_path: Path
) -> None:
    rtl, be = record(), record("lib-lef", "4.0")
    edge = Dependency(be.ref, rtl.ref)
    catalog = Catalog(conan_backend)
    catalog.publish(rtl)
    catalog.publish(be, dependencies=(rtl.ref,))
    stored = catalog.get(rtl.ref)
    assert stored.payload == {"files": ("cpu.rtl",)}
    assert catalog.get_many((be.ref, rtl.ref, be.ref)) == (
        catalog.get(be.ref),
        stored,
        catalog.get(be.ref),
    )
    assert not catalog.get_many(())
    for query in (
        SearchQuery(kind="rtl"),
        SearchQuery(version="1.0"),
        SearchQuery(name="cpu", kind="rtl", namespace="soc"),
    ):
        assert catalog.search(query) == (stored,)
    assert not catalog.search(SearchQuery(namespace="with/slash"))
    assert not catalog.search(SearchQuery(name="missing"))
    assert not catalog.search(SearchQuery(name=""))
    expected = resolve_published(catalog, be.ref)
    assert expected.dependencies == (edge,)
    assert len(expected.to_networkx()) == 2
    assert catalog.graph.dependencies(be.ref)[0] == stored
    assert catalog.graph.consumers(rtl.ref)[0].ref == be.ref
    assert not catalog.graph.dependencies(be.ref, filters=SearchQuery(kind="missing"))
    _, folder = conan_backend._locate(be.ref)
    assert not (folder / "conan.lock").exists()
    lockfile = catalog.create_lockfile((be.ref,), tmp_path / "workspace/conan.lock")
    assert lockfile.is_file()
    assert resolve_published(catalog, be.ref, lockfile=lockfile) == expected
    catalog.publish(record(version="2.0"))
    assert resolve_published(catalog, be.ref) == expected
    with pytest.raises(IdentityConflict):
        catalog.publish(be)
    with pytest.raises(NotFound):
        catalog.get(record(name="missing").ref)
    catalog.close()
    catalog.close()
    with pytest.raises(BackendUnavailable):
        catalog.search()


def test_conan_reverse_search_uses_each_candidates_graph(conan_backend: ConanBackend) -> None:
    rtl, other = record(), record(version="2.0")
    mid = record("intermediate", "3.0", "other-component")
    be, wrong, unrelated = (record("lib-lef", version) for version in ("4.0", "5.0", "6.0"))
    catalog = Catalog(conan_backend)
    catalog.publish(rtl)
    catalog.publish(other)
    catalog.publish(mid, dependencies=(rtl.ref,))
    catalog.publish(be, dependencies=(mid.ref,))
    catalog.publish(wrong, dependencies=(other.ref,))
    catalog.publish(unrelated)
    query = SearchQuery(namespace="soc", name="cpu", kind="lib-lef")
    assert tuple(r.ref for r in catalog.find_dependents(rtl.ref, candidates=query)) == (be.ref,)
    assert not catalog.find_dependents(rtl.ref, candidates=query, transitive=False)
    assert tuple(r.ref for r in catalog.find_dependents(other.ref, candidates=query)) == (
        wrong.ref,
    )
    assert not catalog.find_dependents(rtl.ref, candidates=SearchQuery(kind="rtl"))
    incoming = catalog.graph.consumers(rtl.ref)
    assert tuple(n.ref for n in incoming) == (mid.ref,)
    with pytest.raises(NotFound):
        catalog.find_dependents(record(name="absent").ref, candidates=query)


def test_conan_rejects_conflicting_direct_requirements(conan_backend: ConanBackend) -> None:
    root, first, second = record("lib-lef"), record(), record(version="2.0")
    first = conan_backend.publish(first)
    second = conan_backend.publish(second)
    with pytest.raises(CatalogError):
        conan_backend.publish(root, dependencies=(first.ref, second.ref))
    assert conan_backend.get_many((first.ref, second.ref)) == (first, second)


def test_native_conan_diamond_conflict_is_a_domain_error(conan_backend: ConanBackend) -> None:
    root = record("lib-lef")
    left, right = record(name="left"), record(name="right")
    first, second = record(), record(version="2.0")
    first = conan_backend.publish(first)
    second = conan_backend.publish(second)
    left = conan_backend.publish(left, dependencies=(first.ref,))
    right = conan_backend.publish(right, dependencies=(second.ref,))
    with pytest.raises(VersionConflict):
        conan_backend.publish(root, dependencies=(left.ref, right.ref))
    assert conan_backend.get_many((left.ref, right.ref)) == (left, right)


def test_external_ci_recipe_is_readable(conan_backend: ConanBackend, tmp_path: Path) -> None:
    rtl, be = record(), record("lib-lef", "4.0")
    conan_backend.publish(rtl)
    build = tmp_path / "external-ci"
    write_recipe(build, be, (to_conan_reference(rtl.ref),))
    cli = conan_backend._client
    cli.run(
        "lock",
        "create",
        str(build),
        "--lockfile=",
        "--lockfile-out",
        str(build / "conan.lock"),
        "--no-remote",
        *cli.profile_args,
    )
    cli.run("export", str(build), "--no-remote")
    assert conan_backend.get_many((be.ref,))[0].ref == be.ref
    assert resolve_published(conan_backend, be.ref).dependencies == (Dependency(be.ref, rtl.ref),)


@pytest.mark.parametrize("damage", ["missing", "bad-marker", "noncanonical-recipe"])
def test_corrupt_recipe_is_an_error(conan_backend: ConanBackend, damage: str) -> None:
    rtl, be = record(), record("lib-lef")
    conan_backend.publish(rtl)
    conan_backend.publish(be, dependencies=(rtl.ref,))
    _, folder = conan_backend._locate(be.ref)
    if damage == "missing":
        (folder / "record.json").unlink()
    elif damage == "bad-marker":
        (folder / "hwcatalog.json").write_text('{"format":999}')
    else:
        (folder / "conanfile.py").write_text("raise RuntimeError('must not execute')\n")
    with pytest.raises(InvalidPackage):
        resolve_published(conan_backend, be.ref)


def test_multiple_revisions_are_not_silently_selected(
    conan_backend: ConanBackend, tmp_path: Path
) -> None:
    original = record()
    conan_backend.publish(original)
    folder = tmp_path / "replacement"
    write_recipe(folder, replace(original, payload={"different": True}))
    conan_backend._client.create_lockfile(folder)
    conan_backend._client.export(folder)
    with pytest.raises(IdentityConflict):
        conan_backend.get_many((original.ref,))


def test_upload_failure_is_reported_for_the_single_package(
    conan_backend: ConanBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = record()

    def fail_upload(reference: str) -> None:
        assert reference.startswith("soc.cpu.rtl/1.0#")
        raise BackendUnavailable("simulated lost response")

    monkeypatch.setattr(conan_backend._client, "upload", fail_upload)
    with pytest.raises(BackendUnavailable, match="simulated lost response"):
        conan_backend.publish(root)


def test_command_os_error_is_not_not_found(
    conan_backend: ConanBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hwcatalog._internal.backends import conan_client

    def fail(*args: object, **kwargs: object) -> object:
        raise OSError("API initialization failed")

    monkeypatch.setattr(conan_client, "ConanAPI", fail)
    with pytest.raises(BackendUnavailable, match="API initialization failed"):
        conan_backend.get_many((record().ref,))


def test_native_graph_exposes_conans_dependencies_and_topological_order(
    conan_backend: ConanBackend,
) -> None:
    # Conan's native runtime class has no typing metadata.
    from conan.internal.graph.graph import DepsGraph  # type: ignore[import-untyped]

    root = record(name="aaa-root")
    left, right = record(name="aaa-left"), record(name="bbb-right")
    shared = record(name="zzz-shared")
    conan_backend.publish(shared)
    conan_backend.publish(left, dependencies=(shared.ref,))
    conan_backend.publish(right, dependencies=(shared.ref,))
    conan_backend.publish(root, dependencies=(left.ref, right.ref))
    graph, _ = conan_backend._load_graph(root.ref)
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
    assert dependencies.host[to_conan_reference(shared.ref)].ref.name == "soc.zzz-shared.rtl"
    edges = (
        Dependency(root.ref, left.ref),
        Dependency(root.ref, right.ref),
        Dependency(left.ref, shared.ref),
        Dependency(right.ref, shared.ref),
    )
    assert resolve_published(conan_backend, root.ref).dependencies == tuple(
        sorted(edges, key=lambda edge: (edge.source, edge.target))
    )


def test_native_graph_uses_explicit_home_without_changing_process_state(
    conan_backend: ConanBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = record()
    conan_backend.publish(root)
    ambient_home = tmp_path / "unrelated-home"
    monkeypatch.setenv("CONAN_HOME", str(ambient_home))
    cwd = Path.cwd()
    graph, _ = conan_backend._load_graph(root.ref)
    assert tuple(graph.root.conanfile.dependencies.host.topological_sort.values()) == ()
    assert graph.root.conanfile.recipe_folder is not None
    assert Path(graph.root.conanfile.recipe_folder).is_relative_to(conan_backend._client.home)
    assert os.environ["CONAN_HOME"] == str(ambient_home)
    assert Path.cwd() == cwd
    assert not ambient_home.exists()
    conan_backend.close()
    with pytest.raises(BackendUnavailable):
        resolve_published(conan_backend, root.ref)


def test_native_graph_reports_missing_pinned_dependency(conan_backend: ConanBackend) -> None:
    rtl, macro = record(), record("lib-lef")
    conan_backend.publish(rtl)
    conan_backend.publish(macro, dependencies=(rtl.ref,))
    conan_backend._client.run("remove", to_conan_reference(rtl.ref), "--confirm")
    with pytest.raises(BackendUnavailable):
        conan_backend._load_graph(macro.ref)


def test_native_api_failure_is_a_catalog_error(
    conan_backend: ConanBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from conan.errors import ConanException  # type: ignore[import-untyped]

    from hwcatalog._internal.backends import conan_client

    root = record()
    conan_backend.publish(root)

    def fail(*args: object, **kwargs: object) -> object:
        raise ConanException("simulated API failure")

    monkeypatch.setattr(conan_client, "ConanAPI", fail)
    with pytest.raises(BackendUnavailable, match="simulated API failure"):
        conan_backend._load_graph(root.ref)


def test_native_graph_computation_does_not_spawn_a_conan_process(
    conan_backend: ConanBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_subprocess(*args: object, **kwargs: object) -> object:
        raise AssertionError("Catalog operations must use the in-process Conan API")

    monkeypatch.setattr(subprocess, "run", reject_subprocess)
    monkeypatch.setattr(subprocess, "Popen", reject_subprocess)
    root = record()
    conan_backend.publish(root)
    assert conan_backend.get_many((root.ref,))[0].ref == root.ref
    assert conan_backend.search(SearchQuery())[0].ref == root.ref
    assert len(resolve_published(conan_backend, root.ref).records) == 1


def test_native_graph_rejects_an_unlocked_transitive_dependency(
    conan_backend: ConanBackend, tmp_path: Path
) -> None:
    root, middle, leaf = (record(name=name) for name in ("root", "middle", "leaf"))
    conan_backend.publish(leaf)
    conan_backend.publish(middle, dependencies=(leaf.ref,))
    conan_backend.publish(root, dependencies=(middle.ref,))
    lock_path = conan_backend.create_lockfile((root.ref,), tmp_path / "project/conan.lock")
    conan_backend._client.run(
        "lock",
        "remove",
        "--requires=soc.leaf.rtl/*",
        "--lockfile",
        str(lock_path),
        "--lockfile-out",
        str(lock_path),
    )
    with pytest.raises(InvalidPackage):
        conan_backend._load_graph(root.ref, lockfile=lock_path)


def test_default_cache_uses_conan_home_and_stays_bound_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "native-home"
    monkeypatch.setenv("CONAN_HOME", str(home))
    backend = ConanBackend()
    assert backend._client.home == home.resolve()
    other = tmp_path / "later-home"
    monkeypatch.setenv("CONAN_HOME", str(other))
    with Catalog(backend) as catalog:
        catalog.publish(record())
        assert catalog.get(record().ref).ref == record().ref
    assert not other.exists()


def test_default_cache_uses_native_conanrc_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CONAN_HOME", raising=False)
    (tmp_path / ".conanrc").write_text("conan_home=./configured-home\n")
    backend = ConanBackend()
    assert backend._client.home == (tmp_path / "configured-home").resolve()
    backend.close()


def test_invalid_default_cache_is_a_domain_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONAN_HOME", "relative-path")
    with pytest.raises(BackendUnavailable, match="default home"):
        ConanBackend()
