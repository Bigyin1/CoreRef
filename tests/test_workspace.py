"""Native workspaces select working copies without SDK sidecars or path overlays."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError, Conflict, InvalidPackage
from coreref.api.graph import Graph
from coreref.api.query import SearchQuery
from coreref.api.records import InstalledRecord, LocalRecord, Record, Ref
from coreref.api.workspace import Workspace

pytestmark = pytest.mark.conan_remote


@pytest.fixture
def rtl(catalog: Catalog, checkout: Path, git_origin: Path) -> InstalledRecord:
    assert git_origin.is_dir()
    return catalog.publish(
        LocalRecord(Ref("soc.cpu.rtl.default/1.0", catalog=catalog), source=checkout)
    )


def test_open_writes_only_recipe_and_preserves_work(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    source = workspace.open(rtl.ref)
    assert workspace.members == {rtl.ref.without_revision(): source}
    assert (source / "conanfile.py").is_file()
    assert not (source / "record.json").exists()
    assert not (source / "conandata.yml").exists()
    text = (source / "conanfile.py").read_text()
    assert "coreref_local" not in text and "coreref_revision" not in text
    (source / "cpu.sv").write_text("local edits")
    stamp = (source / "conanfile.py").stat().st_mtime_ns
    with patch.object(catalog._client, "open_sources", side_effect=AssertionError("Clone")):
        assert workspace.open(rtl.ref) == source
    assert (source / "cpu.sv").read_text() == "local edits"
    assert (source / "conanfile.py").stat().st_mtime_ns == stamp


def test_native_discovery_supplies_working_sources_instead_of_cached_sources(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cached = rtl.ref.get_with_sources()
    assert not cached.is_editable and cached.source is not None
    assert cached.source.is_relative_to(catalog.cache_directory)
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    source = workspace.open(rtl.ref)
    monkeypatch.chdir(workspace.path.parent)
    graph = rtl.ref.without_revision().graph()
    installed = graph.get_with_sources()[0]
    assert installed.is_editable and installed.source == source
    assert not installed.source.is_relative_to(catalog.cache_directory)
    assert rtl.ref.get_with_sources().source == source
    restored = Record.from_json(json.dumps(installed.to_dict()), catalog=catalog)
    assert isinstance(restored, InstalledRecord) and restored.is_editable
    with pytest.raises(AttributeError):
        installed.is_editable = False  # type: ignore[misc]
    (source / "cpu.sv").write_text("working version")
    ready_source = graph.get()[0].source
    assert ready_source is not None and (ready_source / "cpu.sv").read_text() == "working version"
    workspace.remove(source)
    assert (source / "conanfile.py").is_file()
    assert not rtl.ref.get_with_sources().is_editable
    workspace.add(source)
    assert rtl.ref.get().is_editable


def test_mixed_graph_batches_only_cached_packages_and_supports_filters(
    *,
    catalog: Catalog,
    rtl: InstalledRecord,
    checkout: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = catalog.publish(
        LocalRecord(Ref("soc.parent.rtl.default/1.0", catalog=catalog), source=checkout),
        dependencies=(rtl.ref,),
    )
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    source = workspace.open(rtl.ref)
    monkeypatch.chdir(workspace.path.parent)
    graph = parent.ref.graph()
    with patch.object(catalog._client, "run", wraps=catalog._client.run) as commands:
        selected = graph.get_with_sources(filters=SearchQuery(component="cpu"))
        assert len(selected) == 1 and selected[0].source == source
        assert selected[0].is_editable
        assert not commands.call_args_list
        ready = graph.get_with_sources()
        assert sum(call.args[0] == "install" for call in commands.call_args_list) == 1
    assert len(ready) == 2 and sum(record.is_editable for record in ready) == 1
    with patch.object(catalog._client, "run", side_effect=AssertionError("Conan I/O")):
        assert graph.get_with_sources() == ready


def test_editable_artifact_paths_can_exist_only_after_local_build(
    catalog: Catalog, checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "original.lib"
    artifact.write_text("published library")
    record = catalog.publish(
        LocalRecord(
            Ref("soc.cpu.be.lib-lef/1.0", catalog=catalog),
            source=checkout,
            files={"timing/cpu.lib": artifact},
        )
    )
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    source = workspace.open(record.ref)
    monkeypatch.chdir(workspace.path.parent)
    graph = record.ref.graph()
    editable = graph.get()[0]
    path = source / "artifacts/timing/cpu.lib"
    assert editable.is_editable and editable.files == {"timing/cpu.lib": path}
    assert not path.exists()
    assert graph.get_with_sources()[0].source == source
    path.parent.mkdir(parents=True)
    path.write_text("locally built library")
    assert graph.get()[0].files["timing/cpu.lib"].read_text() == "locally built library"


def test_workspace_consumer_can_save_and_reuse_native_lockfile(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    source = workspace.open(rtl.ref)
    monkeypatch.chdir(workspace.path.parent)
    local = LocalRecord(Ref("soc.top.rtl.default/1.0", catalog=catalog))
    graph = Graph(catalog, local, dependencies=(rtl.ref.without_revision(),))
    lock = graph.create_lockfile(tmp_path / "project.lock")
    locked = Graph(catalog, local, dependencies=(rtl.ref.without_revision(),), lockfile=lock)
    assert locked.get_with_sources()[0].source == source
    assert locked.edges == graph.edges


def test_open_without_repository_source_is_rejected(catalog: Catalog, tmp_path: Path) -> None:
    record = catalog.publish(LocalRecord(Ref("soc.meta.data.default/1.0", catalog=catalog)))
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    with pytest.raises(InvalidPackage, match="no repository source"):
        workspace.open(record.ref)
    assert not workspace.path.exists()


def test_failed_clone_exposes_sdk_error_and_preserves_original_cause(
    catalog: Catalog, rtl: InstalledRecord, git_origin: Path, tmp_path: Path
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    git_origin.rename(tmp_path / "offline.git")
    with pytest.raises(CatalogError, match="Could not obtain repository sources") as error:
        workspace.open(rtl.ref)
    assert error.value.__cause__ is not None
    assert "git clone" not in str(error.value)
    assert not workspace.path.exists()


def test_malformed_workspace_is_reported_without_backend_text(
    catalog: Catalog, tmp_path: Path
) -> None:
    path = tmp_path / "conanws.yml"
    path.write_text("packages: [\n")
    with pytest.raises(CatalogError, match="Could not read the workspace"):
        _ = Workspace(catalog, path).members
    with pytest.raises(InvalidPackage, match=r"conanws\.yml"):
        Workspace(catalog, tmp_path / "workspace.yaml")


def test_a_cached_snapshot_does_not_silently_switch_to_new_editables(
    *,
    catalog: Catalog,
    rtl: InstalledRecord,
    checkout: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = catalog.publish(
        LocalRecord(Ref("soc.parent.rtl.default/1.0", catalog=catalog), source=checkout),
        dependencies=(rtl.ref,),
    )
    snapshot = parent.ref.graph()
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    source = workspace.open(rtl.ref)
    monkeypatch.chdir(workspace.path.parent)
    with pytest.raises(Conflict, match="Workspace changed"):
        snapshot.get_with_sources()
    ready = parent.ref.graph().get_with_sources(filters=SearchQuery(component="cpu"))
    assert ready[0].is_editable and ready[0].source == source


def test_open_does_not_adopt_an_unrelated_existing_directory(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path
) -> None:
    source = tmp_path / "existing"
    source.mkdir()
    (source / "user.txt").write_text("keep me")
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    with pytest.raises(CatalogError, match="Could not obtain repository sources"):
        workspace.open(rtl.ref, directory=source)
    assert (source / "user.txt").read_text() == "keep me"
    assert not (source / "conanfile.py").exists()
    assert not workspace.path.exists()
