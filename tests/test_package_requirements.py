"""Short names and version ranges work through the public package workflows."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from coreref.api.errors import InvalidPackage
from coreref.api.graph import Graph
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord, Record, Ref, Requirement
from coreref.testing import LocalServer, Sandbox
from coreref.tools.cli import create_deps_app
from coreref.tools.manifest import ManifestFile


def test_short_name_has_no_representation_fields() -> None:
    ref = Ref.from_fields("shared", "std_lib", version="2.0", revision="abc123")
    coordinates = {
        "namespace": "shared",
        "component": "std_lib",
        "version": "2.0",
        "revision": "abc123",
    }
    assert str(ref) == "shared.std_lib/2.0#abc123"
    assert ref.view is None and ref.type is None
    assert ref.to_dict() == coordinates
    assert Ref.from_fields(**coordinates) == ref
    assert Record(ref).to_dict()["ref"] == coordinates
    assert json.loads(LocalRecord(ref).to_json())["ref"] == ref.without_revision().to_dict()
    with pytest.raises(InvalidPackage, match="both view and type"):
        Ref.from_fields("shared", "std_lib", view="rtl", version="2.0")


@pytest.mark.parametrize("version", ["1.0", "[]", "[>]", "[>=1.0]#abc123", "[>=1.0/2.0]"])
def test_requirement_accepts_only_unpinned_conan_ranges(version: str) -> None:
    with pytest.raises(InvalidPackage):
        Requirement(f"shared.stdlib/{version}")


@pytest.mark.conan_remote
def test_project_lock_pins_resolution_and_export_preserves_the_declared_range(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    env = Sandbox(conan_server, tmp_path / "sandbox", namespace="shared")
    first = env.publish(env.package(Ref("shared.stdlib/1.0"), files={"rtl.sv": "first"}))
    env.publish(LocalRecord(Ref("shared.stdlib/3.0")))
    requirement = Requirement("shared.stdlib/[>=1.0 <3.0]")
    project = env.project(env.ref("soc"), dependencies=(requirement,))
    project = ManifestFile(project.path)
    assert project.dependencies == (requirement,)
    lock = project.create_lockfile(env.catalog)
    newer = env.publish(LocalRecord(Ref("shared.stdlib/2.0")))
    assert project.resolve(env.catalog, update=True).requirements == (newer.ref,)
    locked = project.resolve(env.catalog)
    assert locked.lockfile == lock and locked.requirements == (first.ref,)
    assert locked.get()[0].files["rtl.sv"].read_text() == "first"
    project.record.source = None
    project.record.files["manifest.yml"] = project.path
    published = env.publish(project.record, dependencies=project.dependencies, lockfile=lock)
    consumer = env.new_catalog()
    graph = Graph(consumer, published.ref)
    assert graph.requirements == (newer.ref,)
    assert (
        graph.get((published.ref,))[0].files["manifest.yml"].read_bytes()
        == project.path.read_bytes()
    )
    assert consumer.search_refs(SearchQuery(component="stdlib", version="1.0")) == (first.ref,)


def _publish_locked_block(env: Sandbox, component: str, version: str) -> Ref:
    library = env.publish(LocalRecord(Ref(f"shared.stdlib/{version}")))
    block = env.project(env.ref(component), dependencies=(Requirement("shared.stdlib/[>=1 <2]"),))
    block.record.source = None
    block.create_lockfile(env.catalog)
    graph = block.resolve(env.catalog)
    assert graph.requirements == (library.ref,)
    return env.publish(graph).ref


@pytest.mark.conan_remote
def test_soc_resolves_two_published_ranges_with_its_own_lockfile(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    env = Sandbox(conan_server, tmp_path)
    cpu = _publish_locked_block(env, "cpu", "1.2")
    dma = _publish_locked_block(env, "dma", "1.3")
    consumer = env.new_catalog()
    soc = env.project(env.ref("soc"), dependencies=(cpu, dma))
    soc.create_lockfile(consumer)
    locked = soc.resolve(consumer)
    assert locked.dependencies(cpu) == locked.dependencies(dma)
    assert locked.dependencies(cpu)[0].ref.version == "1.3"
    assert not consumer.get(cpu).files

    newer = env.publish(LocalRecord(Ref("shared.stdlib/1.4")))
    updated = soc.resolve(consumer, update=True)
    assert updated.dependencies(cpu) == updated.dependencies(dma) == (newer,)
    assert soc.resolve(consumer).dependencies(cpu) == locked.dependencies(cpu)


@pytest.mark.conan_remote
def test_published_explicit_pin_keeps_its_revision(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    env = Sandbox(conan_server, tmp_path)
    library = LocalRecord(Ref("shared.stdlib/1.2"))
    first = env.publish(library)
    macro = env.publish(LocalRecord(env.ref("macro")), dependencies=(first.ref,))
    library.payload = {"changed": True}
    second = env.publish(library)
    assert first.ref != second.ref
    assert Graph(env.new_catalog(), macro.ref).requirements == (first.ref,)


def test_dependency_cli_roundtrips_a_range_without_conan(tmp_path: Path) -> None:
    project = ManifestFile.create(Ref("demo.soc.rtl.default/1.0"), tmp_path / "manifest.yml")
    app = create_deps_app(catalog_factory=lambda: pytest.fail("Unexpected Conan client"))
    runner = CliRunner()
    value = "pdk.techlib-n5/[>=1.0 <2.0]"
    added = runner.invoke(app, ["add", value, "--file", str(project.path)])
    assert added.exit_code == 0, added.output
    assert ManifestFile(project.path).dependencies == (Requirement(value),)
    removed = runner.invoke(app, ["remove", value, "--file", str(project.path)])
    assert removed.exit_code == 0, removed.output
    assert ManifestFile(project.path).dependencies == ()
