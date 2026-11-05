"""Conan 2.34 command API boundary, including native graph results.

Conan ships no typing metadata. Untyped imports and runtime casts are confined
to this adapter and the protocols describing its actual return objects.
"""

import platform
from collections.abc import Mapping
from pathlib import Path
from threading import RLock
from typing import Protocol, cast

from conan.api.conan_api import ConanAPI  # type: ignore[import-untyped]
from conan.cli.cli import Cli  # type: ignore[import-untyped]
from conan.errors import ConanException  # type: ignore[import-untyped]
from conan.internal.graph.graph_error import (  # type: ignore[import-untyped]
    GraphConflictError,
    GraphLoopError,
)
from pydantic import BaseModel, Field, RootModel, ValidationError

from ...api.errors import BackendUnavailable, DependencyCycle, InvalidPackage, VersionConflict
from .conan_types import NativeGraph, NativeReference

_API_LOCK = RLock()


class _Commands(Protocol):
    def run(self, cmd: list[str], raise_on_errors: bool) -> object: ...


class _API(Protocol):
    command: _Commands

    @property
    def home_folder(self) -> str: ...


def _resolve_home(home: Path | None) -> Path:
    if home is not None:
        return home.expanduser().resolve()
    try:
        with _API_LOCK:
            return Path(cast(_API, ConanAPI()).home_folder).resolve()
    except (ConanException, OSError) as error:
        raise BackendUnavailable(f"Conan could not resolve its default home: {error}") from error


class RecipeRevisions(BaseModel):
    revisions: dict[str, object] = Field(default_factory=dict)


class RecipeIndex(RootModel[dict[str, dict[str, RecipeRevisions | str]]]):
    pass


class ConanClient:
    def __init__(self, home: Path | None, remote: str | None, timeout: float) -> None:
        self.home = _resolve_home(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.remote = remote
        self.timeout = timeout
        self.profile = self.home / "hwcatalog-profile"
        self.profile.write_text("[settings]\n[options]\n[conf]\n")
        self.build_profile = self.home / "hwcatalog-build-profile"
        operating_system = {"Darwin": "Macos"}.get(platform.system(), platform.system())
        self.build_profile.write_text(f"[settings]\nos={operating_system}\n")

    @property
    def source_args(self) -> tuple[str, ...]:
        return ("--remote", self.remote) if self.remote else ("--no-remote",)

    @property
    def profile_args(self) -> tuple[str, ...]:
        return ("--profile:host", str(self.profile), "--profile:build", str(self.build_profile))

    def run(self, *arguments: str) -> object:
        """Run a command without spawning Conan or changing environment/cwd."""
        try:
            # Commands temporarily change Conan's process-global output settings.
            with _API_LOCK:
                conan = ConanAPI(cache_folder=str(self.home))
                Cli(conan).add_commands()
                result = cast(_API, conan).command.run(
                    [
                        *arguments,
                        "-vquiet",
                        "-cc",
                        "core:non_interactive=True",
                        "-cc",
                        f"core.net.http:timeout={self.timeout}",
                    ],
                    raise_on_errors=False,
                )
                if isinstance(result, Mapping):
                    if "graph" in result:
                        cast(NativeGraph, result["graph"]).report_graph_error()
                    if result.get("conan_error") is not None:
                        raise ConanException(str(result["conan_error"]))
                return result
        except GraphConflictError as exc:
            raise VersionConflict(str(exc)) from exc
        except GraphLoopError as exc:
            raise DependencyCycle(str(exc)) from exc
        except ConanException as exc:
            if "not in lockfile" in str(exc):
                raise InvalidPackage("Recipe dependency is absent from its lockfile") from exc
            raise BackendUnavailable(f"Conan command {arguments[:2]} failed: {exc}") from exc
        except OSError as exc:
            raise BackendUnavailable(f"Conan command {arguments[:2]} failed: {exc}") from exc

    def index(self, pattern: str) -> dict[str, RecipeRevisions]:
        source = ("--remote", self.remote) if self.remote else ()
        result = cast(Mapping[str, object], self.run("list", pattern, *source))
        try:
            groups = RecipeIndex.model_validate(result["results"]).root
        except (ValidationError, KeyError) as exc:
            raise BackendUnavailable("Conan returned an invalid recipe index") from exc
        entries: dict[str, RecipeRevisions] = {}
        for group in groups.values():
            if "error" in group:
                raise BackendUnavailable(f"Conan index failed: {group['error']}")
            entries.update(
                (key, value) for key, value in group.items() if isinstance(value, RecipeRevisions)
            )
        return entries

    def cache_path(self, reference: str, *, download: bool = True) -> Path:
        if self.remote and download:
            self.run("download", reference, "--only-recipe", "--remote", self.remote)
        result = self.run("cache", "path", reference)
        if not isinstance(result, str):
            raise BackendUnavailable("Conan returned an invalid cache path")
        return Path(result)

    def graph(self, directory: Path, *, lockfile: Path | None = None) -> NativeGraph:
        result = cast(
            Mapping[str, object],
            self.run(
                "graph",
                "info",
                str(directory / "conanfile.py"),
                "--lockfile",
                str(lockfile) if lockfile is not None else "",
                *self.profile_args,
                *self.source_args,
            ),
        )
        return cast(NativeGraph, result["graph"])

    def create_lockfile(self, directory: Path, *, seed: Path | None = None) -> None:
        self.run(
            "lock",
            "create",
            str(directory / "conanfile.py"),
            "--lockfile",
            str(seed) if seed else "",
            "--lockfile-out",
            str(directory / "conan.lock"),
            *self.profile_args,
            *self.source_args,
        )

    def create_project_lockfile(
        self, references: tuple[str, ...], output: Path, *, seed: Path | None = None
    ) -> None:
        """Ask Conan to capture a consumer's complete dependency graph."""
        arguments = ["lock", "create"] if references else ["lock", "add"]
        if not references:
            seed = None
        arguments.extend(f"--requires={reference}" for reference in references)
        arguments.extend(("--lockfile", str(seed) if seed is not None else ""))
        arguments.extend(("--lockfile-out", str(output)))
        if references:
            arguments.extend(("--lockfile-clean", *self.profile_args, *self.source_args))
        self.run(*arguments)

    def export(self, directory: Path) -> str:
        result = cast(Mapping[str, object], self.run("export", str(directory), "--no-remote"))
        return cast(NativeReference, result["reference"]).repr_notime()

    def export_package(self, directory: Path, *, lockfile: Path | None = None) -> NativeGraph:
        result = cast(
            Mapping[str, object],
            self.run(
                "export-pkg",
                str(directory),
                "--test-folder=",
                "--skip-binaries",
                "--lockfile",
                str(lockfile) if lockfile is not None else "",
                *self.profile_args,
                *self.source_args,
            ),
        )
        return cast(NativeGraph, result["graph"])

    def extend_lockfile(self, reference: str, source: Path, output: Path) -> None:
        """Add the consumer requirement without resolving another dependency graph."""
        self.run(
            "lock",
            "add",
            f"--requires={reference}",
            "--lockfile",
            str(source),
            "--lockfile-out",
            str(output),
        )

    def install(
        self, reference: str, lockfile: Path | None, output: Path, *, with_sources: bool = False
    ) -> NativeGraph:
        source_args: tuple[str, ...] = ()
        if with_sources:
            logical = reference.split("#", 1)[0]
            source_args = ("--conf:host", f"{logical}:tools.build:download_source=True")
        result = cast(
            Mapping[str, object],
            self.run(
                "install",
                "--requires",
                reference,
                "--lockfile",
                str(lockfile) if lockfile is not None else "",
                "--build=never",
                *source_args,
                "--output-folder",
                str(output),
                *self.profile_args,
                *self.source_args,
            ),
        )
        return cast(NativeGraph, result["graph"])

    def upload(self, reference: str) -> None:
        if self.remote:
            self.run("upload", reference, "--remote", self.remote, "--confirm")
