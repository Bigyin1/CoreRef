"""Reusable Typer commands; requires the coreref[cli] optional dependency."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from ..api.catalog import Catalog
from ..api.errors import CatalogError
from ..api.records import Ref
from ..api.workspace import Workspace
from .manifest import ManifestFile

ProjectDir = Annotated[Path, typer.Option(help="Project containing conanws.yml.")]
Manifest = Annotated[Path, typer.Option("--file", "-f", help="Project YAML manifest.")]
SourceDirectory = Annotated[
    Path | None, typer.Option(help="Repository destination; defaults beside the project.")
]


@contextmanager
def _command_errors() -> Iterator[None]:
    try:
        yield
    except (CatalogError, OSError, ValueError, TypeError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from error


class _WorkspaceCommands:
    def __init__(self, catalog_factory: Callable[[], Catalog]) -> None:
        self._catalog_factory = catalog_factory

    def init(self, project_dir: ProjectDir = Path(".")) -> None:
        """Initialize a native workspace."""
        with _command_errors():
            Workspace(self._catalog_factory(), project_dir / "conanws.yml").init()

    def add(self, directory: Path, project_dir: ProjectDir = Path(".")) -> None:
        """Register an existing package directory with Conan."""
        with _command_errors():
            catalog = self._catalog_factory()
            source = directory.expanduser().resolve()
            workspace = Workspace(catalog, project_dir / "conanws.yml")
            if (source / "conanfile.py").exists():
                workspace.add(source)
            else:
                ManifestFile(catalog, source / "manifest.yml").add_to_workspace(workspace)
            typer.echo(source)

    def open(
        self,
        ref: str,
        project_dir: ProjectDir = Path("."),
        directory: SourceDirectory = None,
    ) -> None:
        """Obtain a package repository and register it in the workspace."""
        with _command_errors():
            catalog = self._catalog_factory()
            path = Workspace(catalog, project_dir / "conanws.yml").open(
                Ref(ref),
                directory=directory,
            )
            typer.echo(path)

    def remove(self, directory: Path, project_dir: ProjectDir = Path(".")) -> None:
        """Unregister a directory while preserving its contents."""
        with _command_errors():
            Workspace(self._catalog_factory(), project_dir / "conanws.yml").remove(directory)
            typer.echo(f"Removed {directory}")

    def list_members(self, project_dir: ProjectDir = Path(".")) -> None:
        """List package references and paths of registered editable packages."""
        with _command_errors():
            workspace = Workspace(self._catalog_factory(), project_dir / "conanws.yml")
            for ref, path in sorted(workspace.members.items()):
                typer.echo(f"{ref}\t{path}")


def create_workspace_app(*, catalog_factory: Callable[[], Catalog] = Catalog) -> typer.Typer:
    """Build a workspace group for app.add_typer(..., name="workspace").

    The factory is called only when a command runs, never for help. It can return
    the flow's configured Catalog; by default Conan uses its normal environment.
    """
    commands = _WorkspaceCommands(catalog_factory)
    app = typer.Typer(
        no_args_is_help=True, add_completion=False, help="Manage editable dependencies."
    )
    app.command("add")(commands.add)
    app.command("init")(commands.init)
    app.command("open")(commands.open)
    app.command("remove")(commands.remove)
    app.command("list")(commands.list_members)
    return app


class _DepsCommands:
    def __init__(self, catalog_factory: Callable[[], Catalog]) -> None:
        self._catalog_factory = catalog_factory

    def init(
        self,
        ref: str,
        file: Manifest = Path("manifest.yml"),
    ) -> None:
        """Create manifest.yml without overwriting an existing file."""
        with _command_errors():
            catalog = self._catalog_factory()
            document = ManifestFile.create(catalog, Ref(ref), file)
            typer.echo(document.path)

    def add(self, ref: str, file: Manifest = Path("manifest.yml")) -> None:
        """Declare a dependency, replacing an existing requirement of the same package."""
        with _command_errors():
            catalog = self._catalog_factory()
            document = ManifestFile(catalog, file)
            document.add(Ref(ref))
            document.save()
            typer.echo(document.path)

    def remove(self, ref: str, file: Manifest = Path("manifest.yml")) -> None:
        """Remove a direct dependency declaration."""
        with _command_errors():
            catalog = self._catalog_factory()
            document = ManifestFile(catalog, file)
            document.remove(Ref(ref))
            document.save()
            typer.echo(document.path)

    def list_dependencies(self, file: Manifest = Path("manifest.yml")) -> None:
        """List direct declarations without contacting the registry."""
        with _command_errors():
            document = ManifestFile(self._catalog_factory(), file)
            for ref in document.dependencies:
                typer.echo(ref)

    def lock(self, file: Manifest = Path("manifest.yml")) -> None:
        """Refresh the lockfile from current declarations and remote revisions."""
        with _command_errors():
            document = ManifestFile(self._catalog_factory(), file)
            typer.echo(document.create_lockfile())

    def install(
        self,
        file: Manifest = Path("manifest.yml"),
        sources: Annotated[bool, typer.Option(help="Also obtain Git sources.")] = True,
    ) -> None:
        """Fetch the full dependency tree in one batch, keeping the existing lockfile."""
        with _command_errors():
            graph = ManifestFile(self._catalog_factory(), file).resolve()
            records = graph.get_with_sources() if sources else graph.get()
            typer.echo(json.dumps([record.to_dict() for record in records], indent=2))


def create_deps_app(*, catalog_factory: Callable[[], Catalog] = Catalog) -> typer.Typer:
    """Build an optional YAML dependency group for a flow's Typer application."""
    commands = _DepsCommands(catalog_factory)
    app = typer.Typer(no_args_is_help=True, add_completion=False, help="Manage YAML dependencies.")
    app.command("init")(commands.init)
    app.command("add")(commands.add)
    app.command("remove")(commands.remove)
    app.command("list")(commands.list_dependencies)
    app.command("lock")(commands.lock)
    app.command("install")(commands.install)
    return app


__all__ = ["create_deps_app", "create_workspace_app"]
