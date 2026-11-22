"""Exercise the common manifest, routes and release inputs used by a tool flow."""

import json
import sys
from importlib.metadata import version
from pathlib import Path

import pytest
from typer.testing import CliRunner

from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError
from coreref.api.records import LocalRecord, Ref, Requirement
from coreref.testing import git_repository
from coreref.tools.artifacts import SliceInputs, clean_commit, committed_slice
from coreref.tools.cli import create_deps_app
from coreref.tools.environment import (
    CheckedCommand,
    CommandPin,
    ImagePin,
    pinned_image,
    python_environment,
)
from coreref.tools.manifest import Manifest, ManifestFile
from coreref.tools.project import Project
from coreref.tools.variants import PackageInput, Variants


def test_manifest_read_and_edit_share_the_same_fields(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yml"
    installed = version("coreref")
    path.write_text(f"""ref: demo.project/1.0
type: custom
flow_version: "{installed}"
release_through: my-stage
options: {{width: [8, 16]}}
dependencies:
  - "demo.rtl/[>=1 <2]"
  - {{name: ram, ref: demo.memory/1.0, options: {{width: 8}}}}
""")
    original = Manifest.load(path, flow_package="coreref")
    editable = ManifestFile(path)
    editable.add("demo.extra/1.0")
    editable.save()
    restored = Manifest.load(path, flow_package="coreref")
    assert restored.options == original.options
    assert restored.release_through == original.release_through
    assert restored.type == "custom"
    assert restored.requirements == (Requirement("demo.rtl/[>=1 <2]"), Ref("demo.extra/1.0"))
    assert restored.configured_dependencies == {
        "ram": PackageInput(Ref("demo.memory/1.0"), {"width": 8})
    }


def test_manifest_checks_active_flow_version(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yml"
    path.write_text("ref: demo.project/1.0\n")
    with pytest.raises(ValueError, match="declare flow_version"):
        Manifest.load(path, flow_package="coreref")
    path.write_text('ref: demo.project/1.0\nflow_version: "0.0.0"\n')
    with pytest.raises(ValueError, match="active environment"):
        Manifest.load(path, flow_package="coreref")
    with pytest.raises(ValueError, match="Install it"):
        Manifest.load(path, flow_package="coreref-nonexistent-flow")


def test_manifest_locks_and_installs_both_dependency_kinds(
    tmp_path: Path, catalog: Catalog
) -> None:
    ordinary = catalog.publish(LocalRecord(Ref("demo.rtl/1.0")))
    generator = tmp_path / "generator.py"
    generator.write_text(
        "def generate(options, source, output):\n"
        '    (output / "model").write_text(options["width"])\n'
    )
    ref = Variants(catalog).publish_generated_recipe(
        Ref("demo.memory/1.0"), options={"width": (8, 16)}, inputs={}, runner=generator
    )
    path = tmp_path / "manifest.yml"
    path.write_text("""ref: demo.project/1.0
dependencies:
  - demo.rtl/1.0
  - {name: ram, ref: demo.memory/1.0, options: {width: 16}}
""")
    document = ManifestFile(path)
    lockfile = document.create_lockfile(catalog)
    project = Project(tmp_path, Manifest.load(path), catalog)
    assert project.lock() == lockfile
    assert tuple(record.ref for record in project.dependencies) == (ordinary.ref,)
    packages = project.install_variants(build_missing=True)
    assert packages["ram"].ref == ref
    assert (packages["ram"].directory / "model").read_text() == "16"

    previous = lockfile.read_bytes()
    with pytest.raises(CatalogError):
        project.lock(PackageInput(Ref("demo.absent/1.0"), {}))
    assert lockfile.read_bytes() == previous


def test_shared_cli_reads_the_standard_flow_fields(tmp_path: Path, catalog: Catalog) -> None:
    path = tmp_path / "manifest.yml"
    path.write_text("ref: demo.project/1.0\ntype: custom\nrelease_through: lint\n")
    app = create_deps_app(catalog_factory=lambda: catalog)
    result = CliRunner().invoke(app, ["install", "--file", str(path)])
    assert result.exit_code == 0, result.exception
    assert json.loads(result.output) == []


def test_committed_slice_checks_external_inputs_and_dirty_checkout(tmp_path: Path) -> None:
    repository = git_repository(tmp_path / "work", {"src/main": "sources"})
    lockfile = tmp_path / "manifest.lock"
    lockfile.write_text("first lock")

    def capture(commit: str) -> SliceInputs:
        return SliceInputs(Ref("demo.project/1.0"), commit, lockfile, (), {})

    with committed_slice(repository, capture) as inputs:
        assert inputs.commit == clean_commit(repository)
    assert clean_commit(repository / "src") is None
    assert clean_commit(tmp_path) is None

    with pytest.raises(ValueError, match="inputs changed"), committed_slice(repository, capture):
        lockfile.write_text("another lock")
    with pytest.raises(ValueError, match="inputs changed"), committed_slice(repository, capture):
        (repository / "unexpected").touch()
    with (
        pytest.raises(ValueError, match="clean Git checkout"),
        committed_slice(repository, capture),
    ):
        pytest.fail("A dirty checkout reached the build")


def test_environment_fingerprints_receipt_and_selected_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    source = tmp_path / "flow"
    source.mkdir()
    path = source / "flow.py"
    path.write_text("print('flow')")
    before = python_environment(("coreref",), source_roots=(source,))
    (source / "README.md").write_text("documentation")
    assert python_environment(("coreref",), source_roots=(source,)) == before
    (tmp_path / ".coreref-requirements.txt").write_text("package==1")
    with_receipt = python_environment(("coreref",), source_roots=(source,))
    assert with_receipt != before
    path.write_text("print('changed')")
    assert python_environment(("coreref",), source_roots=(source,)) != with_receipt


def test_shared_tool_pins_check_exact_versions(tmp_path: Path) -> None:
    executable = tmp_path / "eda"
    executable.write_text("#!/bin/sh\nprintf 'EDA 1.2.3\\n'\n")
    executable.chmod(0o755)
    assert CheckedCommand.resolve(
        {"eda": CommandPin(command=str(executable), version="1.2.3")}, "eda"
    ).executable == str(executable)
    with pytest.raises(ValueError, match="must be"):
        CheckedCommand.resolve({"eda": CommandPin(command=str(executable), version="1.2")}, "eda")
    with pytest.raises(FileNotFoundError, match="on PATH"):
        CheckedCommand.resolve(
            {"eda": CommandPin(command=str(tmp_path / "missing"), version="1.2")}, "eda"
        )
    with pytest.raises(ValueError, match="Declare"):
        CheckedCommand.resolve({}, "eda")
    image = ImagePin(image=f"engine@sha256:{'a' * 64}")
    assert pinned_image({"engine": image}, "engine") == image.image
    with pytest.raises(ValueError, match="differs"):
        pinned_image({"engine": image}, "engine", "engine:latest")
    with pytest.raises(ValueError, match="Declare"):
        pinned_image({}, "engine")
