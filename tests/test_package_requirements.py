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
def test_range_resolution_stays_exact_in_project_lock_and_publication(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    env = Sandbox(conan_server, tmp_path / "sandbox", namespace="shared")
    first = env.publish(env.package(Ref("shared.stdlib/1.0"), files={"rtl.sv": "first"}))
    env.publish(LocalRecord(Ref("shared.stdlib/2.0")))
    requirement = Requirement("shared.stdlib/[>=1.0 <2.0]")
    project = env.project(env.ref("soc"), dependencies=(requirement,))
    project = ManifestFile(project.path)
    assert project.dependencies == (requirement,)
    reader = env.new_catalog()
    lock = project.create_lockfile(reader)
    newer = env.publish(LocalRecord(Ref("shared.stdlib/1.5")))
    assert project.resolve(reader, update=True).requirements == (newer.ref,)
    locked = project.resolve(reader)
    assert locked.lockfile == lock and locked.requirements == (first.ref,)
    assert locked.get()[0].files["rtl.sv"].read_text() == "first"
    project.record.source = None
    published = reader.publish(project.record, dependencies=project.dependencies, lockfile=lock)
    assert Graph(reader, published.ref).requirements == (first.ref,)
    query = SearchQuery(namespace="shared", component="stdlib", version="1.0")
    assert reader.search_refs(query) == (first.ref,)


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
