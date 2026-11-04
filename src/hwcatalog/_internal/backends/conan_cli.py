"""Bounded Conan subprocess adapter; no command invokes a shell."""

import os
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field, RootModel, ValidationError

from ...api.errors import BackendUnavailable, DependencyCycle, InvalidPackage, VersionConflict


class RecipeRevisions(BaseModel):
    revisions: dict[str, object] = Field(default_factory=dict)


class RecipeIndex(RootModel[dict[str, dict[str, RecipeRevisions | str]]]):
    pass


class Requirement(BaseModel):
    direct: bool
    build: bool = False


class GraphNode(BaseModel):
    ref: str
    recipe: str
    dependencies: dict[str, Requirement] = Field(default_factory=dict)
    context: str = "host"
    settings: dict[str, object] = Field(default_factory=dict)
    options: dict[str, object] = Field(default_factory=dict)


class GraphError(BaseModel):
    type: str
    error: str = ""


class GraphData(BaseModel):
    nodes: dict[str, GraphNode]
    root: dict[str, object]
    error: GraphError | None = None


class GraphDocument(BaseModel):
    graph: GraphData


class ExportResult(BaseModel):
    reference: str


class CachePath(BaseModel):
    cache_path: str


class ConanCLI:
    def __init__(self, home: Path, executable: str, remote: str | None, timeout: float) -> None:
        self.home = home.resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.executable = shutil.which(executable) or executable
        self.remote = remote
        self.timeout = timeout
        self.profile = self.home / "hwcatalog-profile"
        self.profile.write_text("[settings]\n[options]\n[conf]\n")

    @property
    def source_args(self) -> tuple[str, ...]:
        return ("--remote", self.remote) if self.remote else ("--no-remote",)

    @property
    def profile_args(self) -> tuple[str, ...]:
        return ("--profile:host", str(self.profile), "--profile:build", str(self.profile))

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(  # noqa: S603 - argument list, configured Conan executable
                [self.executable, *args, "-cc", "core:non_interactive=True"],
                cwd=self.home,
                env={**os.environ, "CONAN_HOME": str(self.home)},
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackendUnavailable(f"Conan could not execute {args[:2]}") from exc
        if check and result.returncode:
            raise BackendUnavailable(f"Conan {args[:2]} failed: {result.stderr.strip()}")
        return result

    def index(self, pattern: str) -> dict[str, RecipeRevisions]:
        source = ("--remote", self.remote) if self.remote else ()
        result = self.run("list", pattern, "--format=json", *source)
        try:
            groups = RecipeIndex.model_validate_json(result.stdout).root
        except ValidationError as exc:
            raise BackendUnavailable("Conan returned an invalid recipe index") from exc
        entries: dict[str, RecipeRevisions] = {}
        for group in groups.values():
            if "error" in group:
                raise BackendUnavailable(f"Conan index failed: {group['error']}")
            entries.update(
                (key, value) for key, value in group.items() if isinstance(value, RecipeRevisions)
            )
        return entries

    def cache_path(self, reference: str) -> Path:
        if self.remote:
            self.run("download", reference, "--only-recipe", "--remote", self.remote)
        result = self.run("cache", "path", reference, "--format=json")
        try:
            return Path(CachePath.model_validate_json(result.stdout).cache_path)
        except ValidationError as exc:
            raise BackendUnavailable("Conan returned an invalid cache path") from exc

    def graph(self, directory: Path, *, locked: bool = True) -> GraphData:
        result = self.run(
            "graph",
            "info",
            str(directory / "conanfile.py"),
            "--format=json",
            "--lockfile",
            str(directory / "conan.lock") if locked else "",
            *self.profile_args,
            *self.source_args,
            check=False,
        )
        try:
            graph = GraphDocument.model_validate_json(result.stdout).graph
        except ValidationError as exc:
            if result.returncode:
                raise BackendUnavailable(f"Conan graph failed: {result.stderr.strip()}") from exc
            raise BackendUnavailable("Conan returned an invalid graph") from exc
        if graph.error:
            self._graph_error(graph.error, result.stderr)
        if result.returncode:
            raise BackendUnavailable(f"Conan graph failed: {result.stderr.strip()}")
        return graph

    @staticmethod
    def _graph_error(error: GraphError, message: str) -> None:
        if error.type == "conflict":
            raise VersionConflict(message)
        if error.type == "loop":
            raise DependencyCycle(message)
        if "not in lockfile" in error.error:
            raise InvalidPackage("Recipe dependency is absent from its lockfile")
        raise BackendUnavailable(f"Conan graph error ({error.type}): {message}")

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

    def export(self, directory: Path) -> str:
        result = self.run("export", str(directory), "--format=json", "--no-remote")
        try:
            return ExportResult.model_validate_json(result.stdout).reference
        except ValidationError as exc:
            raise BackendUnavailable("Conan returned an invalid export reference") from exc

    def upload(self, reference: str) -> None:
        if self.remote:
            self.run("upload", reference, "--remote", self.remote, "--only-recipe", "--confirm")
