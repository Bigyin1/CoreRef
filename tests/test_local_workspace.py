"""Register directories in place, without Git, publication or metadata sidecars."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError, InvalidPackage
from coreref.api.graph import Graph
from coreref.api.records import LocalRecord, Ref
from coreref.api.workspace import Workspace
from coreref.tools.deps import DepsFile

pytestmark = pytest.mark.conan_remote


def local_package(catalog: Catalog, directory: Path, name: str = "cpu") -> DepsFile:
    directory.mkdir(parents=True)
    (directory / f"{name}.sv").write_text(f"module {name}; endmodule\n")
    return DepsFile.create(catalog, Ref(f"soc.{name}.rtl.default/1.0"), directory / "deps.yml")


def test_add_directory_needs_no_git_cache_or_remote(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = local_package(catalog, tmp_path / "existing cpu")
    directory = document.path.parent
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    with patch("requests.sessions.Session.send", side_effect=AssertionError("Network")):
        document.add_to_workspace(workspace)
        stamp = (directory / "conanfile.py").stat().st_mtime_ns
        workspace.add(directory)
        assert (directory / "conanfile.py").stat().st_mtime_ns == stamp
        assert workspace.members == {document.record.ref: directory}
        monkeypatch.chdir(workspace.path.parent)
        graph = Graph(catalog, document.record.ref)
        editable = graph.get_with_sources()[0]
        assert editable.is_editable and editable.source == directory
        assert not (directory / ".git").exists()
        assert not (directory / "record.json").exists()
        assert not (directory / "conandata.yml").exists()
        workspace.remove(directory)
    assert (directory / "cpu.sv").read_text() == "module cpu; endmodule\n"
    workspace.add(directory)


def test_native_recipe_reads_manifest_edits_without_sync(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    util = local_package(catalog, tmp_path / "util", "util")
    cpu = local_package(catalog, tmp_path / "cpu")
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    cpu.add_to_workspace(workspace)
    util.add_to_workspace(workspace)
    monkeypatch.chdir(workspace.path.parent)
    old = Graph(catalog, cpu.record.ref)
    stamp = (cpu.path.parent / "conanfile.py").stat().st_mtime_ns
    cpu.add(util.record.ref)
    cpu.payload = {"changed": True}
    cpu.save()
    with patch("requests.sessions.Session.send", side_effect=AssertionError("Network")):
        graph = Graph(catalog, cpu.record.ref)
        assert graph.requirements == (util.record.ref,)
        assert graph.record.payload == {"changed": True}
        assert tuple(r.ref for r in graph.get_with_sources()) == (util.record.ref, cpu.record.ref)
    assert old.requirements == () and old.record.payload is None
    assert (cpu.path.parent / "conanfile.py").stat().st_mtime_ns == stamp


def test_local_source_and_missing_artifact_paths_come_from_recipe(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = local_package(catalog, tmp_path / "cpu")
    source = document.path.parent / "rtl"
    source.mkdir()
    document.source = "rtl"
    document.files = {"cpu.lib": "build/timing/result.lib"}
    document.save()
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    document.add_to_workspace(workspace)
    monkeypatch.chdir(workspace.path.parent)
    graph = Graph(catalog, document.record.ref)
    editable = graph.get_with_sources()[0]
    artifact = document.path.parent / "build/timing/result.lib"
    assert editable.source == source and editable.files == {"cpu.lib": artifact}
    assert not artifact.exists()
    artifact.parent.mkdir(parents=True)
    artifact.write_text("library")
    assert graph.get()[0].files["cpu.lib"].read_text() == "library"


def test_native_add_preserves_an_existing_user_recipe(catalog: Catalog, tmp_path: Path) -> None:
    directory = tmp_path / "custom"
    directory.mkdir()
    recipe = directory / "conanfile.py"
    text = (
        "from conan import ConanFile\nclass Package(ConanFile):\n"
        '    name = "soc.cpu.rtl.default"\n    version = "1.0"\n'
    )
    recipe.write_text(text)
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    workspace.add(directory)
    assert recipe.read_text() == text
    assert workspace.members == {Ref("soc.cpu.rtl.default/1.0"): directory}


def test_editable_cannot_silently_return_cache_sources(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = local_package(catalog, tmp_path / "cpu")
    document.source = str(catalog.cache_directory / "accidental-cache-source")
    document.save()
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    document.add_to_workspace(workspace)
    monkeypatch.chdir(workspace.path.parent)
    with pytest.raises(InvalidPackage, match="package storage"):
        Graph(catalog, document.record.ref)


def test_public_cache_location_respects_configured_storage(
    catalog: Catalog, tmp_path: Path
) -> None:
    assert catalog.cache_directory == catalog._client.home / "p"
    storage = tmp_path / "custom storage"
    (catalog._client.home / "global.conf").write_text(f"core.cache:storage_path={storage}\n")
    assert catalog.cache_directory == storage


def test_workspace_and_its_packages_can_move(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "environment"
    document = local_package(catalog, root / "cpu")
    workspace = Workspace(catalog, root / "project/conanws.yml")
    document.add_to_workspace(workspace)
    moved = tmp_path / "moved"
    root.rename(moved)
    workspace = Workspace(catalog, moved / "project/conanws.yml")
    monkeypatch.chdir(workspace.path.parent)
    assert workspace.members == {document.record.ref: moved / "cpu"}
    assert catalog.get_with_sources(document.record.ref).source == moved / "cpu"


def test_local_consumer_gets_editable_dependencies_automatically(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = local_package(catalog, tmp_path / "cpu")
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    document.add_to_workspace(workspace)
    monkeypatch.chdir(workspace.path.parent)
    root = LocalRecord(Ref("soc.top.rtl.default/1.0"))
    graph = Graph(catalog, root, dependencies=(document.record.ref,))
    assert graph.get_with_sources()[0].source == document.path.parent


def test_core_add_uses_flow_data_without_reading_manifest_files(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "custom project"
    source = directory / "rtl"
    source.mkdir(parents=True)
    (directory / "deps.yml").write_text("this is deliberately not valid: [")
    util = tmp_path / "util"
    util.mkdir()
    dependency = LocalRecord(Ref("soc.util.rtl.default/1.0"), source=util)
    record = LocalRecord(
        Ref("soc.cpu.rtl.default/1.0"),
        payload={"format": "owned by the flow"},
        source=source,
        files={"result": directory / "build/result"},
    )
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    with patch("requests.sessions.Session.send", side_effect=AssertionError("Network")):
        workspace.add(util, record=dependency)
        workspace.add(directory, record=record, dependencies=(dependency.ref,))
        monkeypatch.chdir(workspace.path.parent)
        graph = Graph(catalog, record.ref)
        assert graph.requirements == (dependency.ref,)
        installed = graph.get_with_sources((record.ref,))[0]
    assert installed.source == source and installed.is_editable
    assert installed.payload == record.payload
    assert installed.files == record.files
    assert not installed.files["result"].exists()
    assert (directory / "deps.yml").read_text() == "this is deliberately not valid: ["
    assert not (directory / "record.json").exists()
    assert not (directory / "conandata.yml").exists()


def test_core_add_requires_a_recipe_or_explicit_description(
    catalog: Catalog, tmp_path: Path
) -> None:
    document = local_package(catalog, tmp_path / "cpu")
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    original = document.path.read_bytes()
    with pytest.raises(CatalogError):
        workspace.add(document.path.parent)
    assert document.path.read_bytes() == original
    assert not (document.path.parent / "conanfile.py").exists()
    assert workspace.members == {}


def test_dependencies_without_record_do_not_initialize_workspace(
    catalog: Catalog, tmp_path: Path
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    dependency = Ref("soc.util.rtl.default/1.0")
    with pytest.raises(InvalidPackage, match="require a local record"):
        workspace.add(tmp_path, dependencies=(dependency,))
    assert not workspace.path.exists()


def test_yaml_adapter_uses_the_selected_manifest_name(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "cpu"
    source = directory / "rtl"
    source.mkdir(parents=True)
    document = DepsFile.create(
        catalog,
        Ref("soc.cpu.rtl.default/1.0"),
        directory / "custom inputs.yaml",
        source="rtl",
    )
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    document.add_to_workspace(workspace)
    stamp = (directory / "conanfile.py").stat().st_mtime_ns
    document.add_to_workspace(workspace)
    assert (directory / "conanfile.py").stat().st_mtime_ns == stamp
    document.payload = {"edited": True}
    document.save()
    monkeypatch.chdir(workspace.path.parent)
    installed = catalog.get_with_sources(document.record.ref)
    assert installed.source == source and installed.payload == {"edited": True}
    assert not (directory / "deps.yml").exists()


def test_dotted_part_with_underscore_component_is_native_editable(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "cpu source"
    directory.mkdir()
    ref = Ref("soc.cpu_core.alu.rtl.default/1.0")
    document = DepsFile.create(catalog, ref, directory / "deps.yml")
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    document.add_to_workspace(workspace)
    assert workspace.members == {ref: directory}
    monkeypatch.chdir(workspace.path.parent)
    graph = Graph(catalog, LocalRecord(Ref("soc.top.rtl.default/1.0")), dependencies=(ref,))
    installed = graph.get_with_sources()[0]
    assert installed.ref == ref and installed.source == directory
    assert installed.is_editable
