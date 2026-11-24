"""Realize stage results and exchange exact paths with a Nix binary cache."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from importlib.util import find_spec
from os import environ
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, TypedDict, cast

from ..api.errors import CatalogError, InvalidPackage, NotFound

if TYPE_CHECKING:
    from ..api.build.route import Route, StageTransform
    from ..api.packages.records import JSON

_RECIPES = Path(__file__).with_name("nix")
_RUNNER = Path(__file__).with_name("nix_builder.py")
_STORE_ENTRY = re.compile(r"/nix/store/[0-9a-z]{32}-[a-zA-Z0-9+._?=-]+")


class _PlanNode(TypedDict):
    name: str
    inputs: dict[str, dict[str, str]]


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _nix_json(value: JSON) -> str:
    """Embed JSON in a Nix expression without interpreting interpolation markers."""
    return json.dumps(json.dumps(value, allow_nan=False)).replace("${", r"\${")


def store_entry(path: Path) -> Path:
    """Get the store object containing a file without resolving symlinks."""
    root = Path(*path.parts[:4])
    if not _STORE_ENTRY.fullmatch(str(root)) or ".." in path.parts:
        raise InvalidPackage(f"Expected an exact Nix store path, got {path}")
    return root


def output_name(index: int) -> str:
    return "out" if index == 0 else f"file{index}"


def transform_reference(transform: StageTransform) -> str:
    name = getattr(transform, "__qualname__", "")
    module = getattr(transform, "__module__", "")
    if not module or module == "__main__" or not name.isidentifier():
        raise ValueError("A Nix transform must be a module-level Python function")
    return f"{module}:{name}"


@dataclass(frozen=True)
class Invocation:
    name: str
    transform: str
    execution: str
    configuration: Mapping[str, JSON]
    inputs: Mapping[str, Path | Mapping[str, str]]
    outputs: tuple[str, ...]
    needs: tuple[str, ...] = ()

    def document(self, modules: Mapping[str, Path]) -> dict[str, JSON]:
        outputs: dict[str, JSON] = {
            output_name(index): name for index, name in enumerate(self.outputs)
        }
        if not outputs:
            outputs["out"] = None
        inputs = {
            name: str(path.resolve(strict=True)) if isinstance(path, Path) else dict(path)
            for name, path in self.inputs.items()
        }
        return {
            "name": self.name,
            "transform": self.transform,
            "configuration": dict(self.configuration),
            "execution": self.execution,
            "outputs": outputs,
            "inputs": inputs,
            "needs": self.needs,
            "modules": {name: str(path) for name, path in modules.items()},
        }


class NixStore:
    """Invoke the installed Nix client without changing its daemon configuration."""

    def __init__(self, cache: str | None, python: Path | None, launcher: tuple[str, ...]) -> None:
        self.cache = cache
        self.python = python
        self.launcher = launcher
        self._package_paths: dict[str, Path] = {}

    def command(self, *arguments: str, stdin: str | None = None) -> str:
        binary = which("nix")
        if binary is None:
            raise CatalogError("Install Nix and make nix available on PATH")
        result = subprocess.run(  # noqa: S603 - argument vector for the configured Nix executable.
            [binary, "--extra-experimental-features", "nix-command flakes", *arguments],
            input=stdin,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        if result.returncode:
            raise CatalogError(result.stderr.strip() or "Nix command failed")
        return result.stdout.strip()

    @cached_property
    def runtime(self) -> Path:
        if self.python is not None:
            path = store_entry(self.python)
            if not (path / "bin/python").is_file():
                raise InvalidPackage(f"No Python interpreter in {path}")
            return path
        return Path(
            self.command(
                "build",
                "--file",
                str(_RECIPES / "runtime.nix"),
                "--no-link",
                "--print-out-paths",
            )
        )

    def source(self, directory: Path, name: str, *, tracked: bool = False) -> Path:
        """Import a project or Python package with a stable store name."""
        directory = directory.resolve(strict=True)
        files = _tracked_files(directory) if tracked else None
        return Path(
            self.command(
                "eval",
                "--impure",
                "--raw",
                "--file",
                str(_RECIPES / "source.nix"),
                "--argstr",
                "directory",
                str(directory),
                "--argstr",
                "name",
                name,
                "--arg-from-stdin",
                "selection",
                "source",
                stdin=json.dumps(files, ensure_ascii=False),
            )
        )

    def prefetch(self, reference: str) -> tuple[Path, dict[str, JSON]]:
        """Fetch a source tree and capture the locked Nix fetcher attributes."""
        data = json.loads(self.command("flake", "prefetch", "--json", "--", reference))
        return Path(data["storePath"]), {**data["locked"], "narHash": data["hash"]}

    def fetch(self, locked: Mapping[str, JSON]) -> Path:
        """Restore a pinned tree through its original Nix fetcher."""
        expression = (
            "{ locked }: { source = (builtins.fetchTree (builtins.fromJSON locked)).outPath; }"
        )
        return Path(
            self.command(
                "eval",
                "--impure",
                "--raw",
                "--expr",
                expression,
                "--arg-from-stdin",
                "locked",
                "source",
                stdin=json.dumps(dict(locked)),
            )
        )

    def add(self, path: Path, name: str) -> Path:
        """Import bytes as a standalone content-addressed store object."""
        return Path(self.command("store", "add", "--name", name, "--", str(path)))

    def add_text(self, name: str, contents: str) -> Path:
        """Write text as a store object independent of paths mentioned in its contents."""
        return Path(
            self.command(
                "eval",
                "--raw",
                "--expr",
                "{ name, contents }: { file = builtins.toFile name contents; }",
                "--argstr",
                "name",
                name,
                "--arg-from-stdin",
                "contents",
                "file",
                stdin=contents,
            )
        )

    def upload(self, paths: Sequence[Path]) -> None:
        if self.cache is None:
            raise CatalogError("Configure a writable Nix binary cache before publishing")
        roots = sorted({str(store_entry(path)) for path in paths})
        if roots:
            self.command("copy", "--to", self.cache, "--", *roots)

    def restore(self, paths: Sequence[Path]) -> None:
        entries = {store_entry(path) for path in paths}
        roots = sorted(str(path) for path in entries if not path.exists())
        if not roots:
            return
        if self.cache is not None:
            self.command("copy", "--from", self.cache, "--", *roots)
            return
        try:
            self.command(
                "build", "--no-link", "--max-jobs", "0", "--option", "builders", "", "--", *roots
            )
        except CatalogError as error:
            raise NotFound(f"Missing from the Nix store: {', '.join(roots)}") from error

    def plan(
        self, route: Route, targets: tuple[str, ...], *, publishing: bool = False
    ) -> dict[str, dict[str, dict[str, str]]]:
        """Ask Nix for the selected nodes in input order and their file producers."""
        for name in targets:
            if route[name].purpose != "release" and publishing:
                raise ValueError(f"Expected a release stage, got {name!r}")
        request = {
            "stages": {
                spec.name: {
                    "needs": spec.needs,
                    "checks": spec.checks,
                    "outputs": {
                        output_name(index): filename for index, filename in enumerate(spec.outputs)
                    },
                }
                for spec in (route[name] for name in route.order)
            },
            "targets": targets,
            "publishing": publishing,
        }
        plan = cast(
            list[_PlanNode],
            json.loads(
                self.command(
                    "eval",
                    "--json",
                    "--file",
                    str(_RECIPES / "route.nix"),
                    "--arg-from-stdin",
                    "request",
                    "plan",
                    stdin=json.dumps(request),
                )
            ),
        )
        return {node["name"]: node["inputs"] for node in plan}

    def realize(
        self,
        stages: Mapping[str, Invocation],
        directory: Path,
        *,
        build_missing: bool = True,
        selected: tuple[str, ...] | None = None,
    ) -> dict[str, Path]:
        """Realize selected outputs across a group of derivations with one Nix build."""
        targets = self._targets(stages, selected)
        if not targets:
            return {}
        plan = {
            name: invocation.document(self._modules(invocation.transform))
            for name, invocation in stages.items()
        }
        results = self._build(targets, plan, directory, build_missing)
        return {
            name: results[invocation.name][output_name(index)] / name
            for invocation in stages.values()
            for index, name in enumerate(invocation.outputs)
            if selected is None or name in selected
        }

    @staticmethod
    def _targets(
        stages: Mapping[str, Invocation],
        selected: tuple[str, ...] | None,
    ) -> tuple[str, ...]:
        if selected is None:
            return tuple(f"{name}^*" for name in stages)
        targets = []
        for name, invocation in stages.items():
            outputs = [
                output_name(index)
                for index, output in enumerate(invocation.outputs)
                if output in selected
            ]
            if outputs:
                targets.append(f"{name}^{','.join(outputs)}")
        return tuple(targets)

    def _build(
        self,
        targets: tuple[str, ...],
        plan: Mapping[str, JSON],
        directory: Path,
        build_missing: bool,
    ) -> dict[str, dict[str, Path]]:
        options: tuple[str, ...] = (
            () if build_missing else ("--max-jobs", "0", "--option", "builders", "")
        )
        if self.cache is not None:
            options += ("--extra-substituters", self.cache)
        first = targets[0].partition("^")[0]
        expression = (
            f"let plan = builtins.fromJSON {_nix_json(plan)}; in "
            "{ stageFile, pythonRoot }: import (/. + stageFile) { inherit plan pythonRoot; }"
        )
        data = json.loads(
            self.command(
                "build",
                "--json",
                "--out-link",
                str(directory / f".nix-{first}"),
                "--file",
                "-",
                *options,
                "--argstr",
                "stageFile",
                str(_RECIPES / "stage.nix"),
                "--argstr",
                "pythonRoot",
                str(self.runtime) if self.python is not None else "",
                *targets,
                stdin=expression,
            )
        )
        return {
            Path(item["drvPath"]).name.partition("-")[2].removesuffix(".drv"): {
                name: Path(path) for name, path in item["outputs"].items()
            }
            for item in data
        }

    def host(
        self,
        invocation: Invocation,
        directory: Path,
        *,
        log: Path,
        build_missing: bool,
        always_run: bool,
    ) -> dict[str, Path]:
        """Run a host transform and keep its exact produced files in the store."""
        plan = invocation.document(self._modules(invocation.transform))
        key = self._host_key(invocation, plan)
        state = directory / f".host-{key}.json"
        if not always_run and state.is_file():
            cached = {name: Path(path) for name, path in json.loads(state.read_bytes()).items()}
            if all(path.is_file() for path in cached.values()):
                return cached
        if not build_missing:
            raise NotFound(f"Host result {invocation.name} was not published")
        with TemporaryDirectory(prefix="host-stage-", dir=directory) as temporary:
            workspace = Path(temporary)
            self._run_host(invocation, workspace, plan, log)
            results = self._import_host_outputs(
                invocation, workspace / "work", directory / f".nix-{invocation.name}"
            )
            result_file = workspace / "results.json"
            result_file.write_text(json.dumps({name: str(path) for name, path in results.items()}))
            result_file.replace(state)
        return results

    def _host_key(self, invocation: Invocation, plan: dict[str, JSON]) -> str:
        document: dict[str, JSON] = {
            **plan,
            "python": str(self.runtime),
            "launcher": [
                [arg, _file_digest(path)] if (path := Path(arg)).is_file() else arg
                for arg in self.launcher
            ],
            "inputs": {
                name: [_file_digest(path), path.stat().st_mode & 0o111]
                for name, path in invocation.inputs.items()
                if isinstance(path, Path)
            },
        }
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode()).hexdigest()

    def _run_host(
        self,
        invocation: Invocation,
        directory: Path,
        plan: dict[str, JSON],
        log: Path,
    ) -> None:
        command = [*self.launcher, str(self.runtime / "bin/python"), str(_RUNNER)]
        try:
            subprocess.run(  # noqa: S603 - explicit host launcher and pinned Python interpreter.
                [*command, str(log)],
                cwd=directory,
                env={**environ, "PYTHONDONTWRITEBYTECODE": "1"},
                input=json.dumps(plan, allow_nan=False),
                encoding="utf-8",
                check=True,
            )
        except subprocess.CalledProcessError as error:
            raise CatalogError(f"Stage {invocation.name} failed. See {log}.") from error

    def _import_host_outputs(
        self, invocation: Invocation, work: Path, link: Path
    ) -> dict[str, Path]:
        if not invocation.outputs:
            return {}
        files = {
            f"{invocation.name}-{index}": str(work / name)
            for index, name in enumerate(invocation.outputs)
        }
        expression = (
            "builtins.mapAttrs (name: file: builtins.path { inherit name; path = /. + file; }) "
            f"(builtins.fromJSON {_nix_json(files)})"
        )
        paths = json.loads(
            self.command(
                "build",
                "--impure",
                "--json",
                "--out-link",
                str(link),
                "--file",
                "-",
                *files,
                stdin=expression,
            )
        )
        return {name: Path(path) for name, path in zip(invocation.outputs, paths, strict=True)}

    def _modules(self, transform: str) -> dict[str, Path]:
        names = sorted({"coreref", transform.split(".", 1)[0].partition(":")[0]})
        return {name: self._module(name) for name in names}

    def _module(self, name: str) -> Path:
        if name not in self._package_paths:
            spec = find_spec(name)
            if spec is None or spec.origin is None:
                raise InvalidPackage(f"Cannot locate the Python package {name}")
            origin = Path(spec.origin)
            package = spec.submodule_search_locations is not None
            path = origin.parent if package else origin
            suffix = "" if package else origin.suffix
            self._package_paths[name] = self.source(path, f"coreref-python-{name}{suffix}")
        return self._package_paths[name]


def _tracked_files(directory: Path) -> dict[str, bool] | None:
    """Keep untracked and ignored files out of a checked-out source snapshot."""
    git = which("git")
    if git is None:
        return None
    result = subprocess.run(  # noqa: S603 - fixed Git query with a separate directory argument.
        [git, "-C", str(directory), "ls-files", "-z", "--", "."],
        capture_output=True,
        check=False,
    )
    tracked = result.stdout.decode("utf-8", "surrogateescape").rstrip("\0").split("\0")
    if result.returncode or "manifest.yml" not in tracked:
        return None
    paths = {directory}
    for file in tracked:
        path = directory / file
        paths.add(path)
        paths.update(parent for parent in path.parents if parent.is_relative_to(directory))
    return dict.fromkeys(map(str, paths), True)
