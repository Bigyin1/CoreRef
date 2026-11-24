"""Dependency snapshots are independent of the active workspace."""

from pathlib import Path
from shutil import copytree

import pytest

from coreref.api.errors import InvalidPackage
from coreref.api.packages import Catalog, Ref
from coreref.api.projects import Manifest, Project

pytestmark = pytest.mark.nix


def _project_tree(tmp_path: Path) -> Path:
    fixture = Path(__file__).parent / "test_projects"
    return copytree(fixture, tmp_path / "projects")


def _project(directory: Path, catalog: Catalog) -> Project:
    return Project(directory, Manifest.load(directory / "manifest.yml"), catalog)


def test_lock_keeps_source_and_editor_override_is_live(tmp_path: Path) -> None:
    projects = _project_tree(tmp_path)
    project = _project(projects / "soc", Catalog(projects))
    lock = project.lock().read_bytes()
    stdlib = Ref("demo.stdlib/1.0")
    original = project.graph.get_sources((stdlib,))[stdlib]
    source = projects / "stdlib" / "rtl/stdlib.sv"
    source.write_text("// local edit", encoding="utf-8")
    cold = _project(project.source, Catalog(projects))
    assert cold.graph.get_sources((stdlib,))[stdlib] == original
    assert cold.graph.info(stdlib).source == original

    editable = _project(project.source, Catalog(projects, editables={stdlib: source.parent.parent}))
    (selected,) = editable.graph.get_sources((stdlib,)).values()
    assert (selected / "rtl/stdlib.sv").read_text() == "// local edit"
    assert editable.graph.info(stdlib).source == original
    assert editable.lockfile.read_bytes() == lock

    project.lock()
    assert project.graph.info(stdlib).source != original
    assert project.graph.info(stdlib).ref == stdlib


def test_shared_dependency_survives_pruning_and_range_is_locked(tmp_path: Path) -> None:
    projects = _project_tree(tmp_path)
    root = projects / "soc" / "manifest.yml"
    root.write_text(
        "ref: demo.soc/1.0\ndependencies: [demo.cpu/1.0, 'demo.stdlib/[>=1 <2]']\n",
        encoding="utf-8",
    )
    newer = copytree(projects / "stdlib", projects / "newer")
    (newer / "manifest.yml").write_text("ref: demo.stdlib/1.1\n", encoding="utf-8")
    (projects / "cpu" / "manifest.yml").write_text(
        "ref: demo.cpu/1.0\ndependencies: ['demo.stdlib/[>=1 <2]']\n",
        encoding="utf-8",
    )
    project = _project(root.parent, Catalog(projects))
    project.lock()
    visible = project.graph.dependencies(transitive=True, stop_at={Ref("demo.cpu/1.0")})
    assert {record.ref for record in visible} == {
        Ref("demo.cpu/1.0"),
        Ref("demo.stdlib/1.1"),
    }


def test_source_cycles_are_reported_before_writing_lock(tmp_path: Path) -> None:
    projects = _project_tree(tmp_path)
    child = projects / "stdlib" / "manifest.yml"
    child.write_text("ref: demo.stdlib/1.0\ndependencies: [demo.soc/1.0]\n", encoding="utf-8")
    project = _project(projects / "soc", Catalog(projects))
    with pytest.raises(InvalidPackage, match="Dependency cycle"):
        project.lock()
    assert not project.lockfile.exists()
