"""Behavioral tests against real Conan recipes and its dependency graph evaluator."""

import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.enums import Direction, LineageDirection, RelationKind
from hwcatalog.api.errors import (
    BackendUnavailable,
    IdentityConflict,
    InvalidPackage,
    InvalidPublication,
    NotFound,
    PartialPublicationError,
    UnsupportedOperation,
    VersionConflict,
)
from hwcatalog.api.models.graph import Relation
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.backends.conan import (
    ConanBackend,
    from_conan_reference,
    to_conan_reference,
    write_recipe,
)

pytestmark = pytest.mark.conan


@pytest.fixture
def conan_executable() -> str:
    path = Path(sys.executable).parent / "conan"
    executable = str(path) if path.is_file() else shutil.which("conan")
    if executable is None:
        pytest.skip("Install development dependencies to run Conan tests")
    return executable


@pytest.fixture
def conan_backend(tmp_path: Path, conan_executable: str) -> ConanBackend:
    return ConanBackend(tmp_path / "home", executable=conan_executable)


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
    edge = Relation(be.ref, rtl.ref, RelationKind.REQUIRES)
    catalog = Catalog(conan_backend)
    catalog.publish((be, rtl), relations=(edge,))
    stored = catalog.get(rtl.ref)
    assert stored.payload == {"files": ("cpu.rtl",)}
    assert catalog.get_many((be.ref, rtl.ref, be.ref)) == (
        catalog.get(be.ref),
        stored,
        catalog.get(be.ref),
    )
    assert not catalog.get_many(())
    assert catalog.capabilities.lockfiles
    assert not catalog.capabilities.atomic_publish
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
    assert expected.consistency == "live"
    assert expected.relations == (edge,)
    assert len(expected.to_networkx()) == 2
    assert catalog.graph.dependencies(be.ref)[0].record == stored
    assert catalog.graph.consumers(rtl.ref)[0].record.ref == be.ref
    assert not catalog.graph.neighbors(
        be.ref, RelationKind.REQUIRES, filters=SearchQuery(kind="missing")
    )
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
        relations=(
            Relation(be.ref, mid.ref, RelationKind.REQUIRES),
            Relation(mid.ref, rtl.ref, RelationKind.REQUIRES),
            Relation(wrong.ref, other.ref, RelationKind.REQUIRES),
        ),
    )
    query = SearchQuery(namespace="soc", name="cpu", kind="lib-lef")
    assert tuple(r.ref for r in catalog.find_dependents(rtl.ref, candidates=query)) == (be.ref,)
    assert not catalog.find_dependents(rtl.ref, candidates=query, transitive=False)
    assert tuple(r.ref for r in catalog.find_dependents(other.ref, candidates=query)) == (
        wrong.ref,
    )
    assert not catalog.find_dependents(rtl.ref, candidates=SearchQuery(kind="rtl"))
    incoming = catalog.graph.neighbors(rtl.ref, RelationKind.REQUIRES, Direction.INCOMING)
    assert tuple(n.record.ref for n in incoming) == (mid.ref,)
    with pytest.raises(NotFound):
        catalog.find_dependents(record(name="absent").ref, candidates=query)


@pytest.mark.parametrize(
    "kind",
    [
        RelationKind.IMPLEMENTS,
        RelationKind.USED,
        RelationKind.PRODUCED,
        RelationKind.BACKWARD_COMPATIBLE_WITH,
    ],
)
def test_conan_rejects_unsupported_relations_before_writes(
    conan_backend: ConanBackend, kind: RelationKind
) -> None:
    a, b = record(), record("lib-lef")
    with pytest.raises(UnsupportedOperation):
        conan_backend.publish((a, b), relations=(Relation(b.ref, a.ref, kind),))
    assert not conan_backend.search(SearchQuery())


def test_conan_explicitly_rejects_unsupported_queries(conan_backend: ConanBackend) -> None:
    ref = record().ref
    with pytest.raises(UnsupportedOperation):
        conan_backend.graph.implementations(ref)
    with pytest.raises(UnsupportedOperation):
        conan_backend.graph.lineage(ref, LineageDirection.UPSTREAM)
    with pytest.raises(UnsupportedOperation):
        conan_backend.graph.dependency_closure(ref, resolve_versions=True)


@pytest.mark.parametrize("attributes", [{"alias": "input"}, {"ordinal": 0}, {"metadata": {"a": 1}}])
def test_conan_rejects_binding_attributes(
    conan_backend: ConanBackend, attributes: dict[str, object]
) -> None:
    a, b = record(), record("lib-lef")
    edge = Relation.from_json(
        json.dumps(
            {
                "source": a.ref.__dict__,
                "target": b.ref.__dict__,
                "kind": "REQUIRES",
                "alias": None,
                "ordinal": None,
                "metadata": {},
                **attributes,
            }
        )
    )
    with pytest.raises(UnsupportedOperation):
        conan_backend.publish((a, b), relations=(edge,))


def test_conan_preflight_conflict_does_not_publish_any_record(conan_backend: ConanBackend) -> None:
    root, first, second = record("lib-lef"), record(), record(version="2.0")
    with pytest.raises(VersionConflict):
        conan_backend.publish(
            (root, first, second),
            relations=(
                Relation(root.ref, first.ref, RelationKind.REQUIRES),
                Relation(root.ref, second.ref, RelationKind.REQUIRES),
            ),
        )
    assert not conan_backend.search(SearchQuery())


def test_native_conan_diamond_conflict_is_a_domain_error(conan_backend: ConanBackend) -> None:
    root = record("lib-lef")
    left, right = record(name="left"), record(name="right")
    first, second = record(), record(version="2.0")
    edges = (
        Relation(root.ref, left.ref, RelationKind.REQUIRES),
        Relation(root.ref, right.ref, RelationKind.REQUIRES),
        Relation(left.ref, first.ref, RelationKind.REQUIRES),
        Relation(right.ref, second.ref, RelationKind.REQUIRES),
    )
    with pytest.raises(VersionConflict):
        conan_backend.publish((root, left, right, first, second), relations=edges)
    assert not conan_backend.search(SearchQuery())


def test_external_ci_recipe_is_readable(conan_backend: ConanBackend, tmp_path: Path) -> None:
    rtl, be = record(), record("lib-lef", "4.0")
    conan_backend.publish((rtl,))
    build = tmp_path / "external-ci"
    write_recipe(build, be, (rtl.ref,))
    cli = conan_backend._cli
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
    assert conan_backend.graph.dependency_closure(be.ref).relations == (
        Relation(be.ref, rtl.ref, RelationKind.REQUIRES),
    )


@pytest.mark.parametrize(
    "damage", ["missing", "incomplete", "self-reference", "bad-marker", "noncanonical-recipe"]
)
def test_corrupt_recipe_is_an_error(conan_backend: ConanBackend, damage: str) -> None:
    rtl, be = record(), record("lib-lef")
    conan_backend.publish((rtl, be), relations=(Relation(be.ref, rtl.ref, RelationKind.REQUIRES),))
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
    conan_backend._cli.create_lockfile(folder)
    conan_backend._cli.export(folder)
    with pytest.raises(IdentityConflict):
        conan_backend.get_many((original.ref,))


def test_partial_failure_reports_confirmed_and_uncertain_refs(
    conan_backend: ConanBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = record(name="aaa"), record(name="zzz")

    def fail_second(reference: str) -> None:
        if "zzz" in reference:
            raise BackendUnavailable("simulated lost response")

    monkeypatch.setattr(conan_backend._cli, "upload", fail_second)
    with pytest.raises(PartialPublicationError) as caught:
        conan_backend.publish((second, first))
    assert caught.value.published == (first.ref,)
    assert caught.value.uncertain == second.ref


def test_cli_failure_is_not_not_found(tmp_path: Path) -> None:
    backend = ConanBackend(tmp_path / "home", executable=str(tmp_path / "no-such-conan"))
    with pytest.raises(BackendUnavailable):
        backend.get_many((record().ref,))


def test_cli_timeout_is_not_not_found(
    conan_backend: ConanBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("conan", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(BackendUnavailable):
        conan_backend.search(SearchQuery())
