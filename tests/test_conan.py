"""Behavioral tests against real Conan recipes and its dependency graph evaluator."""

import json
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
    IdentityConflict,
    InvalidPackage,
    InvalidPublication,
    NotFound,
    PartialPublicationError,
    VersionConflict,
)
from hwcatalog.api.models.graph import Dependency
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.backends.conan import ConanBackend

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


def test_conan_round_trip_search_and_root_lockfile(conan_backend: ConanBackend) -> None:
    rtl, be = record(), record("lib-lef", "4.0")
    edge = Dependency(be.ref, rtl.ref)
    catalog = Catalog(conan_backend)
    catalog.publish((be, rtl), dependencies=(edge,))
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
    expected = catalog.graph.dependency_closure(be.ref)
    assert expected.dependencies == (edge,)
    assert len(expected.to_networkx()) == 2
    assert catalog.graph.dependencies(be.ref)[0] == stored
    assert catalog.graph.consumers(rtl.ref)[0].ref == be.ref
    assert not catalog.graph.dependencies(be.ref, filters=SearchQuery(kind="missing"))
    _, folder = conan_backend._locate(be.ref)
    lock = json.loads((folder / "conan.lock").read_text())
    assert len(lock["requires"]) == 1
    assert lock["requires"][0].startswith("soc.cpu.rtl/1.0#")
    assert not any(value.startswith("soc.cpu.lib-lef/") for value in lock["requires"])
    catalog.publish((record(version="2.0"),))
    assert catalog.graph.dependency_closure(be.ref) == expected
    with pytest.raises(IdentityConflict):
        catalog.publish((be,))
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
    catalog.publish(
        (be, wrong, mid, rtl, other, unrelated),
        dependencies=(
            Dependency(be.ref, mid.ref),
            Dependency(mid.ref, rtl.ref),
            Dependency(wrong.ref, other.ref),
        ),
    )
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


def test_conan_preflight_conflict_does_not_publish_any_record(conan_backend: ConanBackend) -> None:
    root, first, second = record("lib-lef"), record(), record(version="2.0")
    with pytest.raises(VersionConflict):
        conan_backend.publish(
            (root, first, second),
            dependencies=(
                Dependency(root.ref, first.ref),
                Dependency(root.ref, second.ref),
            ),
        )
    assert not conan_backend.search(SearchQuery())


def test_native_conan_diamond_conflict_is_a_domain_error(conan_backend: ConanBackend) -> None:
    root = record("lib-lef")
    left, right = record(name="left"), record(name="right")
    first, second = record(), record(version="2.0")
    edges = (
        Dependency(root.ref, left.ref),
        Dependency(root.ref, right.ref),
        Dependency(left.ref, first.ref),
        Dependency(right.ref, second.ref),
    )
    with pytest.raises(VersionConflict):
        conan_backend.publish((root, left, right, first, second), dependencies=edges)
    assert not conan_backend.search(SearchQuery())


def test_external_ci_recipe_is_readable(conan_backend: ConanBackend, tmp_path: Path) -> None:
    rtl, be = record(), record("lib-lef", "4.0")
    conan_backend.publish((rtl,))
    build = tmp_path / "external-ci"
    write_recipe(build, be, (rtl.ref,))
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
    assert conan_backend.graph.dependency_closure(be.ref).dependencies == (
        Dependency(be.ref, rtl.ref),
    )


@pytest.mark.parametrize(
    "damage", ["missing", "incomplete", "self-reference", "bad-marker", "noncanonical-recipe"]
)
def test_corrupt_recipe_is_an_error(conan_backend: ConanBackend, damage: str) -> None:
    rtl, be = record(), record("lib-lef")
    conan_backend.publish((rtl, be), dependencies=(Dependency(be.ref, rtl.ref),))
    _, folder = conan_backend._locate(be.ref)
    lock = folder / "conan.lock"
    if damage == "missing":
        lock.unlink()
    elif damage == "incomplete":
        lock.write_text(json.dumps({"version": "0.5", "requires": []}))
    elif damage == "self-reference":
        lock.write_text(
            json.dumps({"version": "0.5", "requires": ["soc.cpu.lib-lef/1.0#" + "a" * 32]})
        )
    elif damage == "bad-marker":
        (folder / "hwcatalog.json").write_text('{"format":999}')
    else:
        (folder / "conanfile.py").write_text("raise RuntimeError('must not execute')\n")
    with pytest.raises(InvalidPackage):
        conan_backend.graph.dependency_closure(be.ref)


def test_multiple_revisions_are_not_silently_selected(
    conan_backend: ConanBackend, tmp_path: Path
) -> None:
    original = record()
    conan_backend.publish((original,))
    folder = tmp_path / "replacement"
    write_recipe(folder, replace(original, payload={"different": True}))
    conan_backend._client.create_lockfile(folder)
    conan_backend._client.export(folder)
    with pytest.raises(IdentityConflict):
        conan_backend.get_many((original.ref,))


def test_partial_failure_reports_confirmed_and_uncertain_refs(
    conan_backend: ConanBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = record(name="aaa"), record(name="zzz")

    def fail_second(reference: str) -> None:
        if "zzz" in reference:
            raise BackendUnavailable("simulated lost response")

    monkeypatch.setattr(conan_backend._client, "upload", fail_second)
    with pytest.raises(PartialPublicationError) as caught:
        conan_backend.publish((second, first))
    assert caught.value.published == (first.ref,)
    assert caught.value.uncertain == second.ref


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
    edges = (
        Dependency(root.ref, left.ref),
        Dependency(root.ref, right.ref),
        Dependency(left.ref, shared.ref),
        Dependency(right.ref, shared.ref),
    )
    conan_backend.publish((root, left, right, shared), dependencies=edges)
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
    assert conan_backend.graph.dependency_closure(root.ref).dependencies == tuple(
        sorted(edges, key=lambda edge: edge.to_json())
    )


def test_native_graph_uses_explicit_home_without_changing_process_state(
    conan_backend: ConanBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = record()
    conan_backend.publish((root,))
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
        conan_backend.graph.dependency_closure(root.ref)


def test_native_graph_reports_missing_pinned_dependency(conan_backend: ConanBackend) -> None:
    rtl, macro = record(), record("lib-lef")
    conan_backend.publish((rtl, macro), dependencies=(Dependency(macro.ref, rtl.ref),))
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
    conan_backend.publish((root,))

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
    conan_backend.publish((root,))
    assert conan_backend.get_many((root.ref,))[0].ref == root.ref
    assert conan_backend.search(SearchQuery())[0].ref == root.ref
    assert len(conan_backend.graph.dependency_closure(root.ref).records) == 1


def test_native_graph_rejects_an_unlocked_transitive_dependency(
    conan_backend: ConanBackend,
) -> None:
    root, middle, leaf = (record(name=name) for name in ("root", "middle", "leaf"))
    conan_backend.publish(
        (root, middle, leaf),
        dependencies=(Dependency(root.ref, middle.ref), Dependency(middle.ref, leaf.ref)),
    )
    _, directory = conan_backend._locate(root.ref)
    lock_path = directory / "conan.lock"
    lock = json.loads(lock_path.read_text())
    lock["requires"] = [value for value in lock["requires"] if "soc.leaf.rtl/" not in value]
    lock_path.write_text(json.dumps(lock))
    with pytest.raises(InvalidPackage):
        conan_backend._load_graph(root.ref)


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
        catalog.publish((record(),))
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
