"""Optional YAML adapter with one mutable local record for a CLI command."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from .._internal.recipe import LocalRecipe
from ..api.errors import Conflict, InvalidPackage, NotFound
from ..api.graph import Graph
from ..api.records import JSON, LocalRecord, Ref

if TYPE_CHECKING:
    from ..api.catalog import Catalog
    from ..api.workspace import Workspace


class _YamlManifest(BaseModel):
    """Validate file contents at I/O boundaries; runtime state belongs to LocalRecord."""

    model_config = ConfigDict(extra="forbid", strict=True)
    ref: str
    dependencies: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)
    payload: JsonValue = None


class ManifestFile:
    """Read once, prepare the working record, and save YAML only when explicitly requested."""

    def __init__(self, path: str | Path = "manifest.yml") -> None:
        self._file = Path(path).expanduser().resolve()
        if self.path.suffix.lower() not in {".yaml", ".yml"}:
            raise InvalidPackage("Manifest must use a .yaml or .yml extension")
        try:
            data = _YamlManifest.model_validate(yaml.safe_load(self.path.read_text()))
        except (yaml.YAMLError, ValidationError) as error:
            raise InvalidPackage(f"Invalid manifest {self.path}: {error}") from error
        _ = self.lockfile
        self.record = LocalRecord(
            Ref(data.ref),
            cast(JSON, data.payload),
            source=self.path.parent,
            files={
                name: (self.path.parent / Path(value).expanduser()).resolve()
                for name, value in data.files.items()
            },
        )
        if self.record.ref.revision is not None:
            raise InvalidPackage("A local package root must not specify a revision")
        self._dependencies: list[Ref] = []
        for value in data.dependencies:
            ref = Ref(value)
            if any(current.package_name == ref.package_name for current in self._dependencies):
                raise InvalidPackage("Duplicate package in dependencies")
            self.add(ref)

    @classmethod
    def create(cls, ref: Ref, path: str | Path = "manifest.yml") -> ManifestFile:
        """Create a minimal document exclusively, preserving any existing file."""
        if ref.revision is not None:
            raise InvalidPackage("A local package root must not specify a revision")
        destination = Path(path).expanduser().resolve()
        if destination.suffix.lower() not in {".yaml", ".yml"}:
            raise InvalidPackage("Manifest must use a .yaml or .yml extension")
        content = yaml.safe_dump(
            _YamlManifest(ref=str(ref)).model_dump(exclude_none=True), sort_keys=False
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("x", encoding="utf-8") as stream:
                stream.write(content)
        except FileExistsError as error:
            raise Conflict(f"Dependency file already exists: {destination}") from error
        return cls(destination)

    @property
    def path(self) -> Path:
        return self._file

    @property
    def dependencies(self) -> tuple[Ref, ...]:
        return tuple(self._dependencies)

    @property
    def lockfile(self) -> Path:
        """Each manifest has its own lockfile: manifest.yml -> manifest.lock."""
        lockfile = self.path.with_suffix(".lock")
        if lockfile.resolve() == self.path or (
            lockfile.exists() and self.path.exists() and lockfile.samefile(self.path)
        ):
            raise InvalidPackage("Manifest and lockfile must be different files")
        return lockfile

    def resolve(self, catalog: Catalog, *, update: bool = False) -> Graph:
        """Resolve dependencies around the working record; leave YAML and lockfile unchanged."""
        lockfile = self.lockfile
        return Graph(
            catalog,
            self.record,
            dependencies=self.dependencies,
            lockfile=None if update else lockfile,
        )

    def add_to_workspace(self, workspace: Workspace) -> None:
        """Prepare a YAML-aware editable recipe, then delegate registration to Conan."""
        directory = self.path.parent
        if not (directory / "conanfile.py").exists():
            LocalRecipe(self.record, tuple(map(str, self.dependencies))).write_editable(
                directory, manifest=self.path.name
            )
        workspace.add(directory)

    def create_lockfile(self, catalog: Catalog) -> Path:
        """Resolve current declarations afresh, then save their exact revision selection."""
        return self.resolve(catalog, update=True).create_lockfile(self.lockfile)

    def add(self, ref: Ref) -> None:
        """Add or replace one working requirement; changes are saved explicitly."""
        if ref.package_name == self.record.ref.package_name:
            raise InvalidPackage("A package cannot depend on itself")
        for index, current in enumerate(self._dependencies):
            if current.package_name == ref.package_name:
                self._dependencies[index] = ref
                return
        self._dependencies.append(ref)

    def remove(self, ref: Ref) -> None:
        """Remove logical coordinates; an explicit revision must match the declaration."""
        selected = next(
            (r for r in self._dependencies if r.without_revision() == ref.without_revision()), None
        )
        if selected is None:
            raise NotFound(f"Dependency is not declared: {ref}")
        if ref.revision is not None and ref != selected:
            raise Conflict(f"Declared dependency is {selected}; requested {ref}")
        self._dependencies.remove(selected)

    def save(self) -> None:
        """Save working metadata and dependencies; SCM stays a flow policy outside YAML."""
        data = _YamlManifest(
            ref=str(self.record.ref),
            dependencies=list(map(str, self.dependencies)),
            files={
                name: os.path.relpath(path.expanduser().absolute(), self.path.parent)
                for name, path in self.record.files.items()
            },
            payload=json.loads(self.record.to_json())["payload"],
        )
        content = yaml.safe_dump(data.model_dump(exclude_none=True), sort_keys=False)
        self.path.write_text(content, encoding="utf-8")


__all__ = ["ManifestFile"]
