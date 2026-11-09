"""A directory with deps.yml can be registered in place without Git or publication."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError, Conflict, InvalidPackage
from coreref.api.graph import Graph
from coreref.api.records import LocalRecord, Ref
from coreref.api.workspace import Workspace
from coreref.tools.deps import DepsFile

pytestmark = pytest.mark.conan_remote


def local_package(catalog: Catalog, directory: Path, name: str = "cpu") -> DepsFile:
    directory.mkdir(parents=True)
    (directory / f"{name}.sv").write_text(f"module {name}; endmodule\n")
    return DepsFile.create(
        catalog, Ref(f"soc.{name}.rtl.default/1.0", catalog=catalog), directory / "deps.yml"
    )


def test_add_directory_needs_no_git_cache_or_remote(catalog: Catalog, tmp_path: Path) -> None:
    directory = local_package(catalog, tmp_path / "existing cpu").path.parent
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    with (
        patch("requests.sessions.Session.send", side_effect=AssertionError("Network")),
        patch.object(catalog._client, "run", wraps=catalog._client.run) as commands,
    ):
        ref = workspace.add_local(directory)
        stamp = (directory / "conanfile.py").stat().st_mtime_ns
        assert workspace.add_local(directory) == ref
        assert (directory / "conanfile.py").stat().st_mtime_ns == stamp
        assert workspace.members == {ref: directory}
        assert [call.args[:2] for call in commands.call_args_list] == [
            ("workspace", "init"),
            ("workspace", "add"),
        ]
        graph = ref.graph(workspace=workspace)
        editable = graph.get_with_sources()[0]
        assert ref.revision is None and editable.source == directory
        assert workspace.add(ref) == editable
        assert not (directory / ".git").exists()
        workspace.remove(ref)
    assert (directory / "cpu.sv").read_text() == "module cpu; endmodule\n"
    assert workspace.add_local(directory) == ref


def test_local_graph_reads_dependencies_and_edits(catalog: Catalog, tmp_path: Path) -> None:
    util = local_package(catalog, tmp_path / "util", "util")
    cpu = local_package(catalog, tmp_path / "cpu")
    cpu.add(util.record.ref)
    cpu.save()
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    top = LocalRecord(Ref("soc.top.rtl.default/1.0", catalog=catalog))
    with patch("requests.sessions.Session.send", side_effect=AssertionError("Network")):
        ref = workspace.add_local(cpu.path.parent)
        workspace.add_local(util.path.parent)
        graph = Graph(catalog, top, dependencies=(ref,), workspace=workspace)
        assert graph.dependencies(ref)[0].ref == util.record.ref
        assert tuple(r.ref for r in graph.get_with_sources()) == (util.record.ref, ref)
        (cpu.path.parent / "cpu.sv").write_text("local changes")
        source = graph.get_with_sources((ref,))[0].source
        assert source is not None and (source / "cpu.sv").read_text() == "local changes"
        lock = graph.create_lockfile(tmp_path / "project.lock")
        assert (
            Graph(catalog, top, dependencies=(ref,), workspace=workspace, lockfile=lock).edges
            == graph.edges
        )


def test_local_source_and_file_paths_come_from_manifest(catalog: Catalog, tmp_path: Path) -> None:
    document = local_package(catalog, tmp_path / "cpu")
    source = document.path.parent / "rtl"
    source.mkdir()
    document.source = "rtl"
    document.files = {"cpu.lib": "build/timing/result.lib"}
    document.save()
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    ref = workspace.add_local(document.path.parent)
    graph = ref.graph(workspace=workspace)
    editable = graph.get_with_sources()[0]
    assert editable.source == source
    artifact = document.path.parent / "build/timing/result.lib"
    assert editable.files == {"cpu.lib": artifact} and not artifact.exists()
    artifact.parent.mkdir(parents=True)
    artifact.write_text("library")
    assert graph.get()[0].files["cpu.lib"].read_text() == "library"


def test_existing_recipe_and_other_directory_are_preserved(
    catalog: Catalog, tmp_path: Path
) -> None:
    directory = local_package(catalog, tmp_path / "cpu").path.parent
    recipe = directory / "conanfile.py"
    recipe.write_text("user recipe")
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    with pytest.raises(Conflict, match="already exist"):
        workspace.add_local(directory)
    assert recipe.read_text() == "user recipe" and not workspace.path.exists()
    recipe.unlink()
    workspace.add_local(directory)
    other = local_package(catalog, tmp_path / "another cpu").path.parent
    with pytest.raises(Conflict, match="already uses"):
        workspace.add_local(other)
    assert not (other / "conanfile.py").exists()


def test_local_package_does_not_claim_a_published_revision(
    catalog: Catalog, tmp_path: Path
) -> None:
    document = local_package(catalog, tmp_path / "cpu")
    published = catalog.publish(document.record)
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    workspace.add_local(document.path.parent)
    with pytest.raises((Conflict, InvalidPackage), match=r"revision|Revision"):
        published.ref.graph(workspace=workspace)


def test_failed_registration_keeps_directory_and_can_retry(
    catalog: Catalog, tmp_path: Path
) -> None:
    directory = local_package(catalog, tmp_path / "cpu").path.parent
    workspace = Workspace(catalog, tmp_path / "project/conanws.yml")
    run = catalog._client.run

    def fail_add(*arguments: str, workspace: Path | None = None) -> object:
        if arguments[:2] == ("workspace", "add"):
            raise CatalogError("Registration failed")
        return run(*arguments, workspace=workspace)

    with (
        patch.object(catalog._client, "run", side_effect=fail_add),
        pytest.raises(CatalogError, match="Registration failed"),
    ):
        workspace.add_local(directory)
    assert (directory / "cpu.sv").is_file()
    assert workspace.add_local(directory).component == "cpu"


def test_local_workspace_can_move_with_its_packages(catalog: Catalog, tmp_path: Path) -> None:
    root = tmp_path / "environment"
    directory = local_package(catalog, root / "cpu").path.parent
    workspace = Workspace(catalog, root / "project/conanws.yml")
    ref = workspace.add_local(directory)
    moved = tmp_path / "moved"
    root.rename(moved)
    workspace = Workspace(catalog, moved / "project/conanws.yml")
    assert workspace.members == {ref: moved / "cpu"}
    assert ref.graph(workspace=workspace).get_with_sources()[0].source == moved / "cpu"
