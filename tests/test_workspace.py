"""Native Conan workspaces select local recipes and retain untracked developer files."""

from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
import yaml  # type: ignore[import-untyped]
from dulwich import porcelain
from dulwich.repo import Repo

from coreref._internal.recipe import ConsumerRecipe, EditableRecipe
from coreref._internal.types import NativeGraph
from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError, Conflict, InvalidPackage, NotFound
from coreref.api.graph import Graph
from coreref.api.records import InstalledRecord, LocalRecord, Ref
from coreref.api.workspace import Workspace

pytestmark = pytest.mark.conan_remote


@pytest.fixture
def rtl(catalog: Catalog, checkout: Path, git_origin: Path) -> InstalledRecord:
    assert git_origin.is_dir()
    return catalog.publish(
        LocalRecord(
            Ref("soc.cpu.rtl.default/1.0", catalog=catalog),
            {"filelist": "rtl.f"},
            source=checkout,
        )
    )


def test_native_files_live_in_project_and_checkout_and_remain_untracked(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    with Repo.init(project):
        workspace = Workspace(catalog, project / "conanws.yml")
        editable = workspace.add(rtl.ref.without_revision())
        assert editable.source == tmp_path / "soc.cpu.rtl.default-1.0"
        assert editable.source is not None
        untracked = porcelain.status(editable.source).untracked
        assert {b"conanfile.py", b"conandata.yml", b"record.json"}.issubset(untracked)
        assert {b"conanws.yml", b"conanws.py"}.issubset(porcelain.status(project).untracked)
    document = yaml.safe_load(workspace.path.read_text())
    assert document["packages"] == [
        {"ref": str(rtl.ref.without_revision()), "path": "../soc.cpu.rtl.default-1.0"}
    ]
    assert not (project / ".coreref").exists()
    assert not (project / "workspace.yaml").exists()
    assert not (catalog._client.home / "editable_packages.json").exists()
    assert not tuple(catalog._client.home.rglob("cpu.sv"))
    assert Workspace(catalog, workspace.path).members == {rtl.ref: editable.source}


def test_native_graph_and_install_discover_workspace_from_project_cwd(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    editable = workspace.add(rtl.ref)
    assert editable.source is not None
    (editable.source / "cpu.sv").write_text("local RTL edits")
    monkeypatch.chdir(workspace.path.parent)
    graph = rtl.ref.without_revision().graph()
    assert graph.editables == (editable,)
    assert graph.record.ref == rtl.ref
    with patch.object(catalog._client, "run", side_effect=AssertionError("Unexpected Conan I/O")):
        assert graph.get_with_sources()[0].source == editable.source
        assert graph.get() == graph.get_with_sources()
        source = graph.get()[0].source
        assert source is not None and (source / "cpu.sv").read_text() == "local RTL edits"
    consumer = tmp_path / "consumer"
    ConsumerRecipe((str(rtl.ref),)).write(consumer)
    result = cast(
        dict[str, object],
        catalog._client.run(
            "install", str(consumer), "--lockfile=", "--build=never", "--no-remote"
        ),
    )
    native = cast(NativeGraph, result["graph"])
    package = native.root.conanfile.dependencies.host[str(rtl.ref.without_revision())]
    assert any(node.recipe == "Editable" for node in native.nodes)
    assert package.package_folder is not None
    assert Path(package.package_folder).resolve() == editable.source
    assert package.folders.source_folder is not None
    assert Path(package.folders.source_folder).resolve() == editable.source


def test_local_recipe_requirements_are_used_instead_of_published_graph(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path
) -> None:
    extra = catalog.publish(LocalRecord(Ref("soc.extra.rtl.default/1.0", catalog=catalog)))
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    editable = workspace.add(rtl.ref)
    assert editable.source is not None
    data_path = editable.source / "conandata.yml"
    data = yaml.safe_load(data_path.read_text())
    data["requires"] = [str(extra.ref)]
    data_path.write_text(yaml.safe_dump(data))
    with patch.object(catalog._client, "download_recipe", side_effect=AssertionError("Download")):
        graph = rtl.ref.without_revision().graph(workspace=workspace)
    assert graph.requirements == (extra.ref,)
    assert graph.dependencies(transitive=True)[0].ref == extra.ref
    assert graph.editables[0].source == editable.source
    assert rtl.ref.graph().requirements == ()


def test_repeated_add_preserves_edits_metadata_and_base_revision(
    catalog: Catalog, rtl: InstalledRecord, checkout: Path, tmp_path: Path
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    first = workspace.add(rtl.ref.without_revision())
    assert first.source is not None
    (first.source / "cpu.sv").write_text("local modifications")
    recipe = first.source / "conanfile.py"
    stamp = recipe.stat().st_mtime_ns
    second = catalog.publish(LocalRecord(rtl.ref.without_revision(), {"updated": True}, checkout))
    with patch.object(catalog._client, "open_sources", side_effect=AssertionError("Clone")):
        assert workspace.add(rtl.ref.without_revision()).ref == rtl.ref
        assert recipe.stat().st_mtime_ns == stamp
        assert (first.source / "cpu.sv").read_text() == "local modifications"
        with pytest.raises(Conflict):
            workspace.add(second.ref)
    assert rtl.ref.without_revision().graph(workspace=workspace).record.ref == rtl.ref
    assert rtl.ref.without_revision().graph().record.ref == second.ref
    with pytest.raises(Conflict, match="base revision"):
        second.ref.graph(workspace=workspace)


def test_remove_through_native_conan_keeps_source_and_allows_readd(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    first = workspace.add(rtl.ref)
    assert first.source is not None
    (first.source / "cpu.sv").write_text("uncommitted work")
    catalog._client.run("workspace", "remove", str(first.source), workspace=workspace.path.parent)
    assert workspace.members == {}
    with pytest.raises(NotFound):
        workspace.remove(rtl.ref)
    with patch.object(catalog._client, "open_sources", side_effect=AssertionError("Clone")):
        assert workspace.add(rtl.ref).source == first.source
    workspace.remove(rtl.ref.without_revision())
    assert workspace.members == {}
    assert (first.source / "cpu.sv").read_text() == "uncommitted work"
    assert (first.source / "conanfile.py").is_file()


def test_editable_artifact_paths_may_not_exist_until_local_build(
    catalog: Catalog, checkout: Path, tmp_path: Path
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
    editable = workspace.add(record.ref)
    assert editable.source is not None
    path = editable.source / "artifacts/timing/cpu.lib"
    assert editable.files == {"timing/cpu.lib": path}
    assert not path.exists()
    graph = record.ref.graph(workspace=workspace)
    assert graph.get()[0].files["timing/cpu.lib"] == path
    assert graph.get_with_sources()[0].source == editable.source
    path.parent.mkdir(parents=True)
    path.write_text("locally built library")
    assert graph.get()[0].files["timing/cpu.lib"].read_text() == "locally built library"
    assert record.ref.get().files["timing/cpu.lib"].read_text() == "published library"


def test_packages_without_scm_are_rejected_before_clone(catalog: Catalog, tmp_path: Path) -> None:
    unsupported = catalog.publish(LocalRecord(Ref("soc.meta.data.default/1.0", catalog=catalog)))
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    with (
        patch.object(catalog._client, "open_sources", side_effect=AssertionError("Clone")),
        pytest.raises(InvalidPackage, match="SCM sources"),
    ):
        workspace.add(unsupported.ref)
    assert not workspace.path.exists()


def test_unrelated_destination_is_never_overwritten(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path
) -> None:
    destination = tmp_path / "soc.cpu.rtl.default-1.0"
    destination.mkdir()
    (destination / "user.txt").write_text("keep me")
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    with pytest.raises(Conflict, match="destination already exists"):
        workspace.add(rtl.ref)
    assert (destination / "user.txt").read_text() == "keep me"
    assert not workspace.path.exists()


def test_failed_git_open_does_not_register_partial_checkout(
    catalog: Catalog, rtl: InstalledRecord, git_origin: Path, tmp_path: Path
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    git_origin.rename(tmp_path / "offline.git")
    with pytest.raises(CatalogError, match="could not open"):
        workspace.add(rtl.ref)
    assert not workspace.path.exists()
    assert not (tmp_path / "soc.cpu.rtl.default-1.0").exists()
    assert not tuple(tmp_path.glob("coreref-editable-*"))
    (tmp_path / "offline.git").rename(git_origin)
    assert workspace.add(rtl.ref).source is not None


def test_multiple_workspaces_move_with_relative_paths(
    catalog: Catalog, rtl: InstalledRecord, checkout: Path, tmp_path: Path
) -> None:
    environment = tmp_path / "environment"
    first = Workspace(catalog, environment / "first/conanws.yml")
    original = first.add(rtl.ref)
    updated = catalog.publish(LocalRecord(rtl.ref.without_revision(), {"updated": True}, checkout))
    second = Workspace(catalog, environment / "second/conanws.yml")
    other = second.add(updated.ref, directory=environment / "other-sources")
    assert original.source != other.source
    environment.rename(tmp_path / "moved")
    restored = Workspace(catalog, tmp_path / "moved/first/conanws.yml").add(rtl.ref)
    assert restored.source == tmp_path / "moved/soc.cpu.rtl.default-1.0"
    assert restored.source is not None and (restored.source / "cpu.sv").is_file()


def test_mixed_graph_installs_only_noneditable_sources_and_keeps_snapshot(
    catalog: Catalog, rtl: InstalledRecord, checkout: Path, tmp_path: Path
) -> None:
    parent = catalog.publish(
        LocalRecord(
            Ref("soc.parent.rtl.default/1.0", catalog=catalog),
            source=checkout,
        ),
        dependencies=(rtl.ref,),
    )
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    editable = workspace.add(rtl.ref)
    graph = parent.ref.graph(workspace=workspace)
    with patch.object(catalog._client, "run", wraps=catalog._client.run) as run:
        ready = graph.get_with_sources()
        assert sum(call.args[0] == "install" for call in run.call_args_list) == 1
        assert next(r for r in ready if r.ref == rtl.ref).source == editable.source
        assert next(r for r in ready if r.ref == parent.ref).source is not None
    workspace.remove(rtl.ref)
    with patch.object(catalog._client, "run", side_effect=AssertionError("Conan I/O")):
        assert graph.get_with_sources((rtl.ref,))[0].source == editable.source
    assert not parent.ref.graph(workspace=workspace).editables


def test_invalid_workspace_is_reported_by_conan(catalog: Catalog, tmp_path: Path) -> None:
    path = tmp_path / "conanws.yml"
    path.write_text("packages: [\n")
    with pytest.raises(CatalogError, match="workspace"):
        _ = Workspace(catalog, path).members
    with pytest.raises(InvalidPackage, match=r"conanws\.yml"):
        Workspace(catalog, tmp_path / "workspace.yaml")


def test_native_workspace_graph_can_save_lock_and_publish_a_consumer(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    editable = workspace.add(rtl.ref)
    local = LocalRecord(Ref("soc.top.rtl.default/1.0", catalog=catalog))
    graph = Graph(catalog, local, dependencies=(rtl.ref.without_revision(),), workspace=workspace)
    lock = graph.create_lockfile(tmp_path / "project/conan.lock")
    locked = Graph(catalog, local, dependencies=(rtl.ref,), lockfile=lock, workspace=workspace)
    assert locked.get_with_sources()[0].source == editable.source
    published = catalog.publish(locked)
    assert published.ref.dependency_refs() == (rtl.ref,)


def test_editable_install_uses_native_package_root_for_missing_artifacts(
    catalog: Catalog, checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "cpu.lib"
    artifact.write_text("published")
    record = catalog.publish(
        LocalRecord(
            Ref("soc.cpu.be.lib-lef/1.0", catalog=catalog),
            source=checkout,
            files={"cpu.lib": artifact},
        )
    )
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    editable = workspace.add(record.ref)
    monkeypatch.chdir(workspace.path.parent)
    installed = record.ref.get_with_sources()
    assert installed.source == editable.source
    assert installed.files == editable.files
    assert not installed.files["cpu.lib"].exists()


@pytest.mark.parametrize("error", [OSError, Conflict])
def test_failed_recipe_preparation_cleans_only_new_checkout_and_can_retry(
    catalog: Catalog, rtl: InstalledRecord, tmp_path: Path, error: type[OSError] | type[Conflict]
) -> None:
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    source = tmp_path / "soc.cpu.rtl.default-1.0"

    def fail_recipe(recipe: EditableRecipe) -> None:
        (recipe.source / "record.json").write_text("partial metadata")
        raise error("injected recipe preparation failure")

    with patch.object(EditableRecipe, "write", fail_recipe), pytest.raises(error):
        workspace.add(rtl.ref)
    assert not source.exists()
    assert not workspace.path.exists()
    assert workspace.members == {}
    editable = workspace.add(rtl.ref)
    assert editable.source == source
    assert (source / "cpu.sv").is_file()
    assert workspace.members == {rtl.ref: source}
