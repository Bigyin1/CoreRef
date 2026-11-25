"""Build project targets and manage local inputs with the CoreRef Python API."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer
from coreref import Project

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
workspace = typer.Typer(help="Manage local input overrides.", no_args_is_help=True)
app.add_typer(workspace, name="workspace")

ProjectOption = Annotated[str, typer.Option(help="Project flake reference.")]
_COMMANDS = {"build", "deps", "lock", "workspace"}


@app.command(context_settings={"allow_extra_args": True, "allow_interspersed_args": False})
def build(
    ctx: typer.Context,
    target: str,
    project: Annotated[str, typer.Argument(help="Project flake reference.")] = ".",
) -> None:
    """Build a target and forward trailing arguments to Nix."""
    options = ctx.args
    # With no PROJECT, Typer can assign the first Nix option to that position.
    if project.startswith("-"):
        options = [project, *options]
        project = "."
    for path in Project(project).build(target, options=options):
        typer.echo(path)


@app.command()
def deps(project: ProjectOption = ".") -> None:
    """List the project's direct flake inputs."""
    for dep in Project(project).dependencies():
        columns = (dep.name, dep.revision, dep.editable)
        typer.echo("\t".join(str(value) for value in columns if value))


@app.command()
def lock(
    inputs: Annotated[list[str] | None, typer.Argument(help="Inputs to update.")] = None,
    project: ProjectOption = ".",
) -> None:
    """Lock missing inputs or update the named inputs."""
    Project(project).lock(*(inputs or ()))


@workspace.command()
def add(input_name: str, directory: Path, *, project: ProjectOption = ".") -> None:
    """Use an existing checkout."""
    typer.echo(Project(project).add_editable(input_name, directory))


@workspace.command()
def checkout(
    input_name: str,
    directory: Annotated[Path | None, typer.Argument()] = None,
    *,
    project: ProjectOption = ".",
) -> None:
    """Clone the input at the locked revision and use it locally."""
    typer.echo(Project(project).checkout(input_name, directory))


@workspace.command()
def remove(input_name: str, *, project: ProjectOption = ".") -> None:
    """Restore the locked input."""
    Project(project).remove_editable(input_name)


@workspace.command("list")
def list_editables(project: ProjectOption = ".") -> None:
    """Show the active local checkouts."""
    for name, directory in Project(project).editables().items():
        typer.echo(f"{name}\t{directory}")


def main(argv: Sequence[str] | None = None) -> None:
    """Run the CLI, also accepting a target name as shorthand for build."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and not arguments[0].startswith("-") and arguments[0] not in _COMMANDS:
        arguments.insert(0, "build")
    try:
        app(args=arguments, prog_name="coreref-flow")
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
    except (OSError, ValueError) as exc:
        typer.echo(f"coreref-flow: {exc}", err=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
