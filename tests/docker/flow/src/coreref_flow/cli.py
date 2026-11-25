"""Build project targets and manage local inputs with the CoreRef Python API."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence

from coreref import Project

_COMMANDS = {"build", "deps", "lock", "venv", "workspace", "-h", "--help"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coreref-flow", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="Build a named target")
    build.add_argument("target")
    build.add_argument("project", nargs="?", default=".", help="Project flake reference")
    build.add_argument("options", nargs=argparse.REMAINDER, help="Additional nix build options")

    deps = commands.add_parser("deps", help="List the project's direct flake inputs")
    deps.add_argument("--project", default=".")

    lock = commands.add_parser("lock", help="Lock missing inputs or update the named inputs")
    lock.add_argument("inputs", nargs="*")
    lock.add_argument("--project", default=".")

    venv = commands.add_parser("venv", help="Install Flow in a venv using the project's flake")
    venv.add_argument("directory", nargs="?", default=".venv")
    venv.add_argument("--project", default=".")

    workspace = commands.add_parser("workspace", help="Manage local input overrides")
    actions = workspace.add_subparsers(dest="action", required=True)
    for name, description in (
        ("add", "Use an existing checkout"),
        ("checkout", "Clone the input at the locked revision and use it locally"),
        ("remove", "Restore the locked input"),
        ("list", "Show the active local checkouts"),
    ):
        action = actions.add_parser(name, help=description)
        action.add_argument("--project", default=".")
        if name != "list":
            action.add_argument("input")
        if name == "add":
            action.add_argument("directory")
        if name == "checkout":
            action.add_argument("directory", nargs="?")
    return parser


def _workspace(project: Project, args: argparse.Namespace) -> None:
    match args.action:
        case "add":
            print(project.add_editable(args.input, args.directory))
        case "checkout":
            print(project.checkout(args.input, args.directory))
        case "remove":
            project.remove_editable(args.input)
        case "list":
            for name, directory in project.editables().items():
                print(f"{name}\t{directory}")


def _show_dependencies(project: Project) -> None:
    for dep in project.dependencies():
        columns = (dep.name, dep.revision, dep.editable)
        print("\t".join(str(value) for value in columns if value))


def _run(args: argparse.Namespace) -> None:
    project = Project(args.project)
    match args.command:
        case "build":
            paths = project.build(args.target, options=args.options)
            sys.stdout.writelines(f"{path}\n" for path in paths)
        case "deps":
            _show_dependencies(project)
        case "workspace":
            _workspace(project, args)
        case "lock":
            project.lock(*args.inputs)
        case "venv":
            sys.stdout.write(project.run("venv", args.directory))


def app(argv: Sequence[str] | None = None) -> int:
    """Dispatch a project operation or report its failure to the terminal."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] not in _COMMANDS:
        arguments.insert(0, "build")
    args = _parser().parse_args(arguments)
    try:
        _run(args)
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    except (OSError, ValueError) as exc:
        print(f"coreref-flow: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(app())
