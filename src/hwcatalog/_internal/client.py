"""Conan 2.34 command API boundary, including native graph results.

Conan ships no typing metadata. Untyped imports and runtime casts are confined
to this adapter and the protocols describing its actual return objects.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

from conan.api.conan_api import ConanAPI  # type: ignore[import-untyped]
from conan.api.output import ConanOutput  # type: ignore[import-untyped]
from conan.api.subapi.lockfile import LockfileAPI  # type: ignore[import-untyped]
from conan.cli.cli import Cli  # type: ignore[import-untyped]
from conan.errors import ConanException  # type: ignore[import-untyped]
from conan.internal.errors import (  # type: ignore[import-untyped]
    ConanReferenceDoesNotExistInDB,
    NotFoundException,
)
from conan.internal.graph.graph_error import (  # type: ignore[import-untyped]
    GraphConflictError,
    GraphLoopError,
    GraphMissingError,
)
from pydantic import BaseModel, Field, RootModel, ValidationError

from ..api.errors import CatalogError, Conflict, InvalidPackage, NotFound
from .types import NativeGraph, NativeReference


class _Commands(Protocol):
    def run(self, cmd: list[str], raise_on_errors: bool) -> object: ...


class _Remote(Protocol):
    name: str


class _Remotes(Protocol):
    def list(self) -> list[_Remote]: ...


class _API(Protocol):
    command: _Commands
    remotes: _Remotes

    @property
    def home_folder(self) -> str: ...


class RecipeRevisions(BaseModel):
    revisions: dict[str, object] = Field(default_factory=dict)


class RecipeIndex(RootModel[dict[str, dict[str, RecipeRevisions | str]]]):
    pass


class ConanClient:
    def __init__(self, home: Path | None, remote: str | None, timeout: float) -> None:
        if home is not None:
            self.home = home.expanduser().resolve()
        else:
            try:
                self.home = Path(cast(_API, ConanAPI()).home_folder).resolve()
            except (ConanException, OSError) as error:
                raise CatalogError(f"Conan could not resolve its default home: {error}") from error
        self.remote = self._configured_remote(remote)
        self.timeout = timeout

    @property
    def source_args(self) -> tuple[str, ...]:
        return ("--remote", self.remote) if self.remote else ("--no-remote",)

    def run(self, *arguments: str) -> object:
        """Run a command without spawning Conan or changing environment/cwd."""
        try:
            return self._execute(arguments)
        except GraphConflictError as exc:
            raise Conflict(str(exc)) from exc
        except GraphLoopError as exc:
            raise InvalidPackage(str(exc)) from exc
        except ConanException as exc:
            if "not in lockfile" in str(exc):
                raise InvalidPackage("Recipe dependency is absent from its lockfile") from exc
            raise CatalogError(f"Conan command {arguments[:2]} failed: {exc}") from exc
        except OSError as exc:
            raise CatalogError(f"Conan command {arguments[:2]} failed: {exc}") from exc

    def _execute(self, arguments: tuple[str, ...]) -> object:
        conan = ConanAPI(cache_folder=str(self.home))
        Cli(conan).add_commands()
        try:
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
        except (NotFoundException, ConanReferenceDoesNotExistInDB) as exc:
            raise NotFound(str(exc)) from exc
        if isinstance(result, Mapping):
            if "graph" in result:
                self._check_graph(cast(NativeGraph, result["graph"]))
            if result.get("conan_error") is not None:
                raise ConanException(str(result["conan_error"]))
        return result

    def _check_graph(self, graph: NativeGraph) -> None:
        try:
            graph.report_graph_error()
        except GraphMissingError as exc:
            if "not found" in exc.missing_error.lower():
                raise NotFound(str(exc)) from exc
            raise

    def index(self, pattern: str) -> dict[str, RecipeRevisions]:
        source = ("--remote", self.remote) if self.remote else ()
        result = cast(Mapping[str, object], self.run("list", pattern, *source))
        return self._entries(result)

    def _entries(self, result: Mapping[str, object]) -> dict[str, RecipeRevisions]:
        try:
            groups = RecipeIndex.model_validate(result["results"]).root
        except (ValidationError, KeyError) as exc:
            raise CatalogError("Conan returned an invalid recipe index") from exc
        entries: dict[str, RecipeRevisions] = {}
        for group in groups.values():
            if "error" in group:
                raise CatalogError(f"Conan index failed: {group['error']}")
            entries.update(
                (key, value) for key, value in group.items() if isinstance(value, RecipeRevisions)
            )
        return entries

    def download_recipe(self, reference: str) -> Path:
        if self.remote:
            result = cast(
                Mapping[str, object],
                self.run("download", reference, "--only-recipe", "--remote", self.remote),
            )
            entries = self._entries(result)
            logical, _, revision = reference.partition("#")
            if logical not in entries or revision not in entries[logical].revisions:
                raise NotFound(reference)
        return self.cache_path(reference)

    def cache_path(self, reference: str) -> Path:
        result = self.run("cache", "path", reference)
        if not isinstance(result, str):
            raise CatalogError("Conan returned an invalid cache path")
        return Path(result)

    def graph(
        self, directory: Path, *, lockfile: Path | None = None, update: bool = False
    ) -> NativeGraph:
        result = cast(
            Mapping[str, object],
            self.run(
                "graph",
                "info",
                str(directory / "conanfile.py"),
                "--lockfile",
                str(lockfile) if lockfile is not None else "",
                *(("--update",) if update else ()),
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
            *self.source_args,
        )

    def save_lockfile(self, graph: NativeGraph, output: Path) -> None:
        """Let Conan create an opaque lockfile from an already resolved graph."""
        level = ConanOutput.get_output_level()
        try:
            ConanOutput.set_output_level(ConanOutput.valid_log_levels()["quiet"])
            lockfile = LockfileAPI.update_lockfile(None, graph, clean=True)
            LockfileAPI.save_lockfile(lockfile, str(output))
        except (ConanException, OSError) as error:
            raise CatalogError(f"Conan could not save lockfile {output}: {error}") from error
        finally:
            ConanOutput.set_output_level(level)

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
                *self.source_args,
            ),
        )
        return cast(NativeGraph, result["graph"])

    def install(self, reference: str, output: Path, *, with_sources: bool = False) -> NativeGraph:
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
                "",
                "--build=never",
                *source_args,
                "--output-folder",
                str(output),
                *self.source_args,
            ),
        )
        return cast(NativeGraph, result["graph"])

    def install_project(self, directory: Path, sources: Mapping[str, bool]) -> NativeGraph:
        """Install one pinned consumer graph, enabling SCM only for selected packages."""
        source_args = tuple(
            argument
            for reference, enabled in sources.items()
            for argument in ("--conf:host", f"{reference}:tools.build:download_source={enabled}")
        )
        result = cast(
            Mapping[str, object],
            self.run(
                "install",
                str(directory),
                "--lockfile",
                str(directory / "conan.lock"),
                "--build=never",
                "--conf:host",
                "*:tools.build:download_source=False",
                *source_args,
                "--output-folder",
                str(directory / "out"),
                *self.source_args,
            ),
        )
        return cast(NativeGraph, result["graph"])

    def upload(self, reference: str) -> None:
        if self.remote:
            self.run("upload", reference, "--remote", self.remote, "--confirm")

    def _configured_remote(self, requested: str | None) -> str | None:
        if requested is not None:
            return requested
        try:
            remotes = cast(_API, ConanAPI(cache_folder=str(self.home))).remotes.list()
        except (ConanException, OSError) as exc:
            raise CatalogError(f"Conan could not read configured remotes: {exc}") from exc
        if len(remotes) > 1:
            names = ", ".join(remote.name for remote in remotes)
            raise CatalogError(f"Multiple enabled Conan remotes ({names}); specify remote=...")
        return remotes[0].name if remotes else None
