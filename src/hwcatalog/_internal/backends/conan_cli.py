"""Conan storage commands and in-process loading of native dependency graphs."""

import os
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field, RootModel, ValidationError

from ...api.errors import BackendUnavailable
from ...backends.conan_types import NativeGraph
from .conan_native import load_native_graph


class RecipeRevisions(BaseModel):
    revisions: dict[str, object] = Field(default_factory=dict)


class RecipeIndex(RootModel[dict[str, dict[str, RecipeRevisions | str]]]):
    pass


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

    def graph(self, directory: Path) -> NativeGraph:
        return load_native_graph(
            self.home,
            [
                "graph",
                "info",
                str(directory / "conanfile.py"),
                "-vquiet",
                "--lockfile",
                str(directory / "conan.lock"),
                *self.profile_args,
                *self.source_args,
                "-cc",
                "core:non_interactive=True",
                "-cc",
                f"core.net.http:timeout={self.timeout}",
            ],
        )

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
