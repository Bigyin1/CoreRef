"""Embeddable workspace commands use the SDK and native Conan registration."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError
from coreref.api.records import LocalRecord, Ref
from coreref.api.workspace import Workspace
from coreref.tools.cli import create_deps_app, create_workspace_app

runner = CliRunner()


def unavailable_catalog() -> Catalog:
    raise CatalogError("Unavailable test catalog")


def test_help_and_invalid_arguments_do_not_initialize_conan() -> None:
    app = typer.Typer()
    app.add_typer(create_workspace_app(catalog_factory=unavailable_catalog), name="workspace")

    @app.command()
    def simulate() -> None:
        typer.echo("simulation")

    assert runner.invoke(app, ["simulate"]).stdout == "simulation\n"
    assert runner.invoke(app, ["--help"]).exit_code == 0
    for command in ([], ["add"], ["remove"], ["list"]):
        help_result = runner.invoke(app, ["workspace", *command, "--help"])
        assert help_result.exit_code == 0, help_result.output
    assert runner.invoke(app, ["workspace", "add"]).exit_code == 2
    result = runner.invoke(app, ["workspace", "list"])
    assert result.exit_code == 1
    assert "Error: Unavailable test catalog" in result.stderr
    assert "Traceback" not in result.output


@pytest.mark.conan_remote
def test_cli_workspace_lifecycle_preserves_working_copy(
    catalog: Catalog, checkout: Path, git_origin: Path, tmp_path: Path
) -> None:
    assert git_origin.is_dir()
    record = catalog.publish(
        LocalRecord(Ref("soc.cpu.rtl.default/1.0", catalog=catalog), source=checkout)
    )
    app = typer.Typer()
    app.add_typer(create_workspace_app(catalog_factory=lambda: catalog), name="workspace")
    project = tmp_path / "project with spaces"
    destination = tmp_path / "local sources"
    options = ["--project-dir", str(project)]
    result = runner.invoke(app, ["workspace", "list", *options])
    assert result.exit_code == 0 and result.stdout == ""
    assert not project.exists()
    result = runner.invoke(
        app,
        ["workspace", "add", str(record.ref), *options, "--directory", str(destination)],
    )
    assert result.exit_code == 0, result.output
    source = destination / "soc.cpu.rtl.default-1.0"
    assert f"{record.ref}\t{source}" in result.stdout
    (source / "cpu.sv").write_text("local work")
    result = runner.invoke(app, ["workspace", "add", str(record.ref.without_revision()), *options])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["workspace", "list", *options])
    assert result.exit_code == 0 and result.stdout == f"{record.ref}\t{source}\n"
    result = runner.invoke(app, ["workspace", "remove", str(record.ref), *options])
    assert result.exit_code == 0, result.output
    assert (source / "cpu.sv").read_text() == "local work"
    assert Workspace(catalog, project / "conanws.yml").members == {}
    result = runner.invoke(app, ["workspace", "remove", str(record.ref), *options])
    assert result.exit_code == 1
    assert "not editable" in result.stderr


@pytest.mark.conan_remote
def test_cli_reports_invalid_ref_and_workspace_without_tracebacks(
    catalog: Catalog, tmp_path: Path
) -> None:
    app = create_workspace_app(catalog_factory=lambda: catalog)
    invalid = runner.invoke(app, ["add", "not-a-ref", "--project-dir", str(tmp_path)])
    assert invalid.exit_code == 1
    assert "Error:" in invalid.stderr
    assert not (tmp_path / "conanws.yml").exists()
    (tmp_path / "conanws.yml").write_text("packages: [\n")
    malformed = runner.invoke(app, ["list", "--project-dir", str(tmp_path)])
    assert malformed.exit_code == 1
    assert "workspace" in malformed.stderr
    assert "Traceback" not in malformed.output


@pytest.mark.parametrize("factory", [create_deps_app, create_workspace_app])
def test_command_groups_are_lazy(factory: Callable[..., typer.Typer]) -> None:
    group = factory(catalog_factory=unavailable_catalog)
    result = runner.invoke(group, ["--help"])
    assert result.exit_code == 0, result.output


@pytest.mark.conan_remote
def test_yaml_commands_initialize_edit_lock_and_install(
    catalog: Catalog, checkout: Path, git_origin: Path, tmp_path: Path
) -> None:
    assert git_origin.is_dir()
    package = catalog.publish(
        LocalRecord(Ref("soc.cpu.rtl.default/1.0", catalog=catalog), source=checkout)
    )
    app = create_deps_app(catalog_factory=lambda: catalog)
    path = tmp_path / "project/deps.yaml"
    options = ["--file", str(path)]
    result = runner.invoke(app, ["init", "soc.top.rtl.default/1.0", "--source", ".", *options])
    assert result.exit_code == 0, result.output
    original = path.read_bytes()
    result = runner.invoke(app, ["init", "soc.other.rtl.default/1.0", *options])
    assert result.exit_code == 1 and path.read_bytes() == original
    result = runner.invoke(app, ["add", str(package.ref.without_revision()), *options])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["list", *options])
    assert result.stdout == f"{package.ref.without_revision()}\n"
    result = runner.invoke(app, ["lock", *options])
    assert result.exit_code == 0, result.output
    locked = path.with_suffix(".lock").read_bytes()
    for flag in (["--no-sources"], []):
        result = runner.invoke(app, ["install", *options, *flag])
        assert result.exit_code == 0, result.output
        installed = json.loads(result.stdout)
        assert installed[0]["ref"]["revision"] == package.ref.revision
        assert ("source" in installed[0]) == (not flag)
        assert path.with_suffix(".lock").read_bytes() == locked
    result = runner.invoke(app, ["remove", str(package.ref.without_revision()), *options])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["list", *options])
    assert result.exit_code == 0 and result.stdout == ""
    result = runner.invoke(app, ["add", "broken", *options])
    assert result.exit_code == 1 and "Error:" in result.stderr


def test_yaml_lock_command_rejects_an_input_named_like_a_lockfile(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "home")
    app = create_deps_app(catalog_factory=lambda: catalog)
    path = tmp_path / "input.lock"
    content = "ref: soc.top.rtl.default/1.0\n"
    path.write_text(content)
    result = runner.invoke(app, ["lock", "--file", str(path)])
    assert result.exit_code == 1
    assert "extension" in result.stderr
    assert path.read_text() == content
