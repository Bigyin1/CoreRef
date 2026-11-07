"""Editable SCM dependencies use project YAML and ordinary Git working copies."""

import shutil
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
import yaml  # type: ignore[import-untyped]

from hwcatalog._internal.recipe import ConsumerRecipe
from hwcatalog._internal.types import NativeGraph
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import CatalogError, Conflict, InvalidPackage, NotFound
from hwcatalog.api.graph import Graph
from hwcatalog.api.records import InstalledRecord, LocalRecord, Ref
from hwcatalog.api.workspace import Workspace

pytestmark = pytest.mark.conan_remote


@pytest.fixture
def rtl(catalog: Catalog, checkout: Path, git_origin: Path) -> InstalledRecord:
    assert git_origin.is_dir()
    return catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog),
            {"filelist": "rtl.f"},
            source=checkout,
        )
    )


def test_add_get_and_reload_keep_conan_files_private(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path
) -> None:
    workspace = Workspace(catalog, tmp_path / "project" / "workspace.yaml")
    with (
        patch.object(catalog._client, "install", side_effect=AssertionError("Unexpected install")),
        patch.object(
            catalog._client, "install_project", side_effect=AssertionError("Unexpected install")
        ),
    ):
        editable = workspace.add(rtl.ref.without_revision())
        assert editable.ref == rtl.ref
        assert editable.source == tmp_path / "soc.cpu.rtl.default-1.0"
        assert editable.source is not None and (editable.source / ".git").is_dir()
        assert (editable.source / "cpu.sv").read_text() == "module cpu; endmodule\n"
        assert not (editable.source / "conanfile.py").exists()
        assert not (workspace.path.parent / "conanws.yml").exists()
        assert not tuple(catalog._client.home.rglob("cpu.sv"))
        document = yaml.safe_load(workspace.path.read_text())
        assert document == {"packages": {str(rtl.ref): "../soc.cpu.rtl.default-1.0"}}
        assert Workspace(catalog, workspace.path).add(rtl.ref) == editable
    assert not (catalog._client.home / "editable_packages.json").exists()


def test_graph_uses_editable_source_and_preserves_working_changes(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path
) -> None:
    workspace = Workspace(catalog, tmp_path / "project" / "workspace.yaml")
    editable = workspace.add(rtl.ref)
    assert editable.source is not None
    (editable.source / "cpu.sv").write_text("module modified_cpu; endmodule\n")
    graph = Graph(
        catalog,
        LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=catalog)),
        dependencies=(rtl.ref.without_revision(),),
        workspace=workspace,
    )
    with patch.object(catalog._client, "run", side_effect=AssertionError("Unexpected Conan I/O")):
        ready = graph.get_with_sources((rtl.ref.without_revision(),))[0]
        assert ready.source == editable.source
        assert ready.source is not None
        assert "modified_cpu" in (ready.source / "cpu.sv").read_text()
        assert graph.get((rtl.ref,))[0] is ready
        assert graph.editables == (ready,)
    assert graph.requirements == (rtl.ref,)
    assert Workspace(catalog, workspace.path).add(rtl.ref).source == ready.source


def test_generated_conan_workspace_really_resolves_an_editable_package(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path
) -> None:
    workspace = Workspace(catalog, tmp_path / "project" / "workspace.yaml")
    editable = workspace.add(rtl.ref)
    directory = tmp_path / "consumer"
    ConsumerRecipe((str(rtl.ref),)).write(directory)
    result = cast(
        dict[str, object],
        catalog._client.run(
            "install",
            str(directory),
            "--lockfile=",
            "--build=never",
            "--no-remote",
            workspace=workspace._file.native_directory,
        ),
    )
    native = cast(NativeGraph, result["graph"])
    package = native.root.conanfile.dependencies.host[str(rtl.ref.without_revision())]
    assert any(node.recipe == "Editable" for node in native.nodes)
    assert package.package_folder is not None
    assert not Path(package.package_folder).is_relative_to(catalog._client.home)
    assert package.folders.source_folder is not None
    assert Path(package.folders.source_folder).resolve() == editable.source
    assert package.recipe_folder is not None
    assert Path(package.recipe_folder).is_relative_to(workspace.path.parent / ".hwcatalog")
    assert Path.cwd() != workspace._file.native_directory
    assert not rtl.ref.graph().editables


def test_repeated_add_keeps_edits_and_the_base_revision_after_new_publication(
    catalog: Catalog, rtl: InstalledRecord, checkout: Path, tmp_path: Path
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/workspace.yaml")
    with patch.object(
        catalog._client, "open_sources", wraps=catalog._client.open_sources
    ) as opened:
        first = workspace.add(rtl.ref.without_revision())
        assert opened.call_count == 1
        assert first.source is not None
        (first.source / "cpu.sv").write_text("local modifications")
        second = catalog.publish(
            LocalRecord(rtl.ref.without_revision(), {"updated": True}, checkout)
        )
        assert workspace.add(rtl.ref.without_revision()).ref == rtl.ref
        assert (first.source / "cpu.sv").read_text() == "local modifications"
        with pytest.raises(Conflict):
            workspace.add(second.ref)
        assert opened.call_count == 1
    graph = rtl.ref.without_revision().graph(workspace=workspace)
    assert graph.record.ref == rtl.ref
    assert graph.get_with_sources((rtl.ref.without_revision(),))[0].source == first.source
    assert rtl.ref.without_revision().graph().record.ref == second.ref


def test_remove_and_readd_preserve_the_git_working_copy(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/workspace.yaml")
    first = workspace.add(rtl.ref)
    assert first.source is not None
    (first.source / "cpu.sv").write_text("uncommitted work")
    workspace.remove(rtl.ref.without_revision())
    assert yaml.safe_load(workspace.path.read_text()) == {"packages": {}}
    with pytest.raises(NotFound):
        workspace.remove(rtl.ref)
    assert (first.source / "cpu.sv").read_text() == "uncommitted work"
    with patch.object(
        catalog._client, "open_sources", side_effect=AssertionError("Unexpected clone")
    ):
        restored = Workspace(catalog, workspace.path).add(rtl.ref)
    assert restored.source == first.source
    assert (first.source / "cpu.sv").read_text() == "uncommitted work"


@pytest.mark.parametrize("with_files", [False, True])
def test_unsupported_package_is_rejected_before_any_clone(
    catalog: Catalog, checkout: Path, tmp_path: Path, with_files: bool
) -> None:
    artifact = tmp_path / "report"
    artifact.write_text("report")
    unsupported = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "be", "be", "lib-lef", "1.0", catalog=catalog),
            source=checkout if with_files else None,
            files={"report": artifact} if with_files else {},
        )
    )
    workspace = Workspace(catalog, tmp_path / "project/workspace.yaml")
    with (
        patch.object(
            catalog._client, "open_sources", side_effect=AssertionError("Unexpected clone")
        ),
        pytest.raises(InvalidPackage, match="SCM sources and no packaged files"),
    ):
        workspace.add(unsupported.ref)
    assert not workspace.path.exists()
    assert not (tmp_path / "soc.cpu.rtl.default-1.0").exists()


def test_unrelated_destination_is_never_overwritten(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path
) -> None:
    destination = tmp_path / "soc.cpu.rtl.default-1.0"
    destination.mkdir()
    (destination / "user.txt").write_text("keep me")
    workspace = Workspace(catalog, tmp_path / "project/workspace.yaml")
    with pytest.raises(Conflict, match="destination already exists"):
        workspace.add(rtl.ref)
    assert (destination / "user.txt").read_text() == "keep me"
    assert not workspace.path.exists()


def test_failed_git_open_cleans_temporary_checkout_and_can_be_retried(
    catalog: Catalog, rtl: InstalledRecord, git_origin: Path, tmp_path: Path
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/workspace.yaml")
    git_origin.rename(tmp_path / "offline.git")
    with pytest.raises(CatalogError, match="could not open"):
        workspace.add(rtl.ref)
    assert not workspace.path.exists()
    assert not (tmp_path / "soc.cpu.rtl.default-1.0").exists()
    assert not tuple(tmp_path.glob("hwcatalog-editable-*"))
    (tmp_path / "offline.git").rename(git_origin)
    assert workspace.add(rtl.ref).source is not None


def test_private_conan_metadata_can_be_rebuilt_without_recloning(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/workspace.yaml")
    first = workspace.add(rtl.ref)
    assert first.source is not None
    (first.source / "cpu.sv").write_text("uncommitted work")
    shutil.rmtree(workspace.path.parent / ".hwcatalog")
    with patch.object(
        catalog._client, "open_sources", side_effect=AssertionError("Unexpected clone")
    ):
        restored = Workspace(catalog, workspace.path).add(rtl.ref)
    assert restored.source == first.source
    assert (first.source / "cpu.sv").read_text() == "uncommitted work"
    assert tuple((workspace.path.parent / ".hwcatalog").rglob("conanws.yml"))


def test_multiple_workspaces_and_moved_project_use_relative_source_paths(
    catalog: Catalog, rtl: InstalledRecord, checkout: Path, tmp_path: Path
) -> None:
    root = tmp_path / "environment"
    first = Workspace(catalog, root / "project/first.yaml")
    original = first.add(rtl.ref)
    updated = catalog.publish(LocalRecord(rtl.ref.without_revision(), {"updated": True}, checkout))
    second = Workspace(catalog, root / "project/second.yaml")
    other = second.add(updated.ref, directory=root / "other-sources")
    assert original.source != other.source
    assert first._file.native_directory != second._file.native_directory
    assert first.add(rtl.ref.without_revision()).ref == rtl.ref
    assert second.add(rtl.ref.without_revision()).ref == updated.ref
    root.rename(tmp_path / "moved")
    restored = Workspace(catalog, tmp_path / "moved/project/first.yaml").add(rtl.ref)
    assert restored.source == tmp_path / "moved/soc.cpu.rtl.default-1.0"
    assert restored.source is not None and (restored.source / "cpu.sv").is_file()


def test_mixed_graph_get_batches_only_the_noneditable_sources(
    catalog: Catalog, rtl: InstalledRecord, checkout: Path, tmp_path: Path
) -> None:
    parent = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "parent", "rtl", "default", "1.0", catalog=catalog),
            source=checkout,
        ),
        dependencies=(rtl.ref,),
    )
    workspace = Workspace(catalog, tmp_path / "project/workspace.yaml")
    editable = workspace.add(rtl.ref)
    graph = parent.ref.graph(workspace=workspace)
    with patch.object(catalog._client, "run", wraps=catalog._client.run) as run:
        ready = graph.get_with_sources((parent.ref, rtl.ref, rtl.ref.without_revision()))
        assert len(ready) == 2
        assert ready[0].source is not None and ready[0].source.is_relative_to(catalog._client.home)
        assert ready[1].source == editable.source
        assert sum(call.args[0] == "install" for call in run.call_args_list) == 1
        assert graph.get_with_sources((rtl.ref, parent.ref))[1] is ready[0]
    assert graph.editables == (ready[1],)
    workspace.remove(rtl.ref)
    assert graph.get_with_sources((rtl.ref,))[0] is ready[1]
    assert not parent.ref.graph(workspace=workspace).editables


def test_workspace_base_and_project_lock_must_agree(
    catalog: Catalog, rtl: InstalledRecord, checkout: Path, tmp_path: Path
) -> None:
    root = LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=catalog))
    original = Graph(catalog, root, dependencies=(rtl.ref.without_revision(),))
    lock = original.create_lockfile(tmp_path / "old.lock")
    workspace = Workspace(catalog, tmp_path / "project/workspace.yaml")
    first = workspace.add(rtl.ref)
    newer = catalog.publish(LocalRecord(rtl.ref.without_revision(), {"updated": True}, checkout))
    current = Graph(catalog, root, dependencies=(newer.ref,))
    current_lock = current.create_lockfile(tmp_path / "current.lock")
    locked = Graph(
        catalog,
        root,
        dependencies=(rtl.ref.without_revision(),),
        lockfile=lock,
        workspace=workspace,
    )
    assert locked.requirements == (rtl.ref,)
    assert locked.get_with_sources((rtl.ref,))[0].source == first.source
    with pytest.raises(CatalogError, match="lockfile"):
        Graph(
            catalog,
            root,
            dependencies=(rtl.ref.without_revision(),),
            lockfile=current_lock,
            workspace=workspace,
        )
    assert not original.editables


def test_workspace_cannot_replace_a_different_pinned_transitive_revision(
    catalog: Catalog, rtl: InstalledRecord, checkout: Path, tmp_path: Path
) -> None:
    parent = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "parent", "rtl", "default", "1.0", catalog=catalog),
        ),
        dependencies=(rtl.ref,),
    )
    newer = catalog.publish(LocalRecord(rtl.ref.without_revision(), {"updated": True}, checkout))
    workspace = Workspace(catalog, tmp_path / "project/workspace.yaml")
    workspace.add(newer.ref)
    with pytest.raises(Conflict, match="graph requires"):
        parent.ref.graph(workspace=workspace)
    assert rtl.ref in {item.ref for item in parent.ref.graph().records}


@pytest.mark.parametrize(
    "text",
    [
        "[invalid YAML",
        "format: 2\npackages: []",
        "packages: invalid",
        "packages: {soc.cpu.rtl.default/1.0: ../cpu}",
        "packages: {bad: ../cpu}",
    ],
)
def test_invalid_workspace_manifest_is_a_domain_error(
    catalog: Catalog, tmp_path: Path, text: str
) -> None:
    path = tmp_path / "workspace.yaml"
    path.write_text(text)
    with pytest.raises(InvalidPackage):
        _ = Workspace(catalog, path).members


def test_members_is_a_detached_manifest_snapshot(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/workspace.yaml")
    editable = workspace.add(rtl.ref)
    members = workspace.members
    assert members == {rtl.ref: editable.source}
    members.clear()
    assert workspace.members == {rtl.ref: editable.source}


def test_graph_captures_members_before_running_conan(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/workspace.yaml")
    editable = workspace.add(rtl.ref)
    resolve = catalog._client.graph

    def unregister_during_resolution(
        directory: Path, *, lockfile: Path | None = None, update: bool = False
    ) -> NativeGraph:
        workspace.path.write_text("packages: {}\n")
        return resolve(directory, lockfile=lockfile, update=update)

    with monkeypatch.context() as change:
        change.setattr(catalog._client, "graph", unregister_during_resolution)
        graph = rtl.ref.without_revision().graph(workspace=workspace)
    assert workspace.members == {}
    assert graph.editables == (editable,)
    with patch.object(catalog._client, "run", side_effect=AssertionError("Unexpected Conan I/O")):
        assert graph.get_with_sources((rtl.ref,))[0].source == editable.source
    assert not rtl.ref.graph(workspace=workspace).editables
