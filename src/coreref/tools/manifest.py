"""The standard CoreRef manifest and its editable project document."""

from __future__ import annotations

import json
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, cast

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from .._internal.recipe import LocalRecipe
from ..api.errors import Conflict, InvalidPackage, NotFound
from ..api.graph import Graph
from ..api.records import JSON, Dependency, LocalRecord, Ref, Requirement, parse_dependency
from .environment import ToolPins
from .variants import PackageInput, Variants

if TYPE_CHECKING:
    from ..api.catalog import Catalog
    from ..api.workspace import Workspace


_OptionValue = str | int | bool


class ConfiguredDependency(BaseModel):
    """A named configuration of a package recipe."""

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1)
    ref: str
    options: dict[str, _OptionValue] = Field(min_length=1)


class Manifest(BaseModel):
    """Build configuration shared by CoreRef projects."""

    model_config = ConfigDict(extra="forbid", strict=True)
    ref: str
    type: str | None = Field(default=None, min_length=1)
    flow_version: str | None = Field(default=None, min_length=1)
    release_through: str | None = Field(default=None, min_length=1)
    dependencies: list[str | ConfiguredDependency] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)
    tools: ToolPins = Field(default_factory=dict)
    options: dict[str, list[_OptionValue]] = Field(default_factory=dict)
    payload: JsonValue = None

    @classmethod
    def load(cls, path: Path, *, flow_package: str | None = None) -> Manifest:
        """Read a manifest from YAML and optionally verify the active flow package."""
        try:
            manifest = cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        except (yaml.YAMLError, ValidationError) as error:
            raise InvalidPackage(f"Invalid manifest {path}: {error}") from error
        if flow_package is not None:
            manifest._check_flow_version(path, flow_package)
        return manifest

    def _check_flow_version(self, path: Path, package: str) -> None:
        if self.flow_version is None:
            raise ValueError(f"{path} must declare flow_version for {package}")
        requirement = f"{package}=={self.flow_version}"
        try:
            installed = version(package)
        except PackageNotFoundError as error:
            raise ValueError(
                f"{path} requires {requirement}. Install it in the project venv."
            ) from error
        if installed != self.flow_version:
            raise ValueError(
                f"{path} requires {requirement}. The active environment has {package}=={installed}."
            )

    def local_record(self, source: Path) -> LocalRecord:
        """Use the project directory as SCM source and as the base for file paths."""
        ref = Ref(self.ref)
        if ref.revision is not None:
            raise InvalidPackage("A local package root must not specify a revision")
        return LocalRecord(
            ref,
            cast(JSON, self.payload),
            source=source,
            files={
                name: (source / Path(path).expanduser()).resolve()
                for name, path in self.files.items()
            },
        )

    @property
    def requirements(self) -> tuple[Dependency, ...]:
        return tuple(parse_dependency(item) for item in self.dependencies if isinstance(item, str))

    @property
    def configured_dependencies(self) -> dict[str, PackageInput]:
        return {
            item.name: PackageInput(Ref(item.ref), item.options)
            for item in self.dependencies
            if isinstance(item, ConfiguredDependency)
        }

    @model_validator(mode="after")
    def _unique_dependencies(self) -> Manifest:
        configured_names = [
            item.name for item in self.dependencies if isinstance(item, ConfiguredDependency)
        ]
        if len(set(configured_names)) != len(configured_names):
            raise ValueError("Duplicate configured dependency name")
        requirements = self.requirements
        if len({ref.package_name for ref in requirements}) != len(requirements):
            raise ValueError("Duplicate package in plain dependencies")
        return self


class ManifestFile:
    """Read once, prepare the working record, and save YAML only when explicitly requested."""

    def __init__(self, path: str | Path = "manifest.yml") -> None:
        self._file = Path(path).expanduser().resolve()
        if self.path.suffix.lower() not in {".yaml", ".yml"}:
            raise InvalidPackage("Manifest must use a .yaml or .yml extension")
        self._manifest = Manifest.load(self.path)
        _ = self.lockfile
        self.record = self._manifest.local_record(self.path.parent)
        self._dependencies: list[Dependency] = []
        for dependency in self._manifest.requirements:
            self.add(dependency)

    @classmethod
    def create(cls, ref: Ref, path: str | Path = "manifest.yml") -> ManifestFile:
        """Create a minimal document exclusively, preserving any existing file."""
        if ref.revision is not None:
            raise InvalidPackage("A local package root must not specify a revision")
        destination = Path(path).expanduser().resolve()
        if destination.suffix.lower() not in {".yaml", ".yml"}:
            raise InvalidPackage("Manifest must use a .yaml or .yml extension")
        content = yaml.safe_dump(
            Manifest(ref=str(ref)).model_dump(exclude_none=True, exclude_unset=True),
            sort_keys=False,
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
    def dependencies(self) -> tuple[Dependency, ...]:
        return tuple(self._dependencies)

    @property
    def configured_dependencies(self) -> dict[str, PackageInput]:
        return self._manifest.configured_dependencies

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
        configured = self.configured_dependencies
        if not configured:
            return self.resolve(catalog, update=True).create_lockfile(self.lockfile)
        return Variants(catalog).create_lockfile(
            self.record,
            requirements=self.dependencies,
            inputs=configured.values(),
            path=self.lockfile,
        )

    def add(self, value: Dependency | str) -> None:
        """Add or replace a dependency declaration; changes are saved explicitly."""
        ref = parse_dependency(value) if isinstance(value, str) else value
        if ref.package_name == self.record.ref.package_name:
            raise InvalidPackage("A package cannot depend on itself")
        for index, current in enumerate(self._dependencies):
            if current.package_name == ref.package_name:
                self._dependencies[index] = ref
                return
        self._dependencies.append(ref)

    def remove(self, value: Dependency | str) -> None:
        """Remove a matching range or logical Ref; an explicit revision must match."""
        ref = parse_dependency(value) if isinstance(value, str) else value
        if isinstance(ref, Requirement):
            if ref not in self._dependencies:
                raise NotFound(f"Dependency is not declared: {ref}")
            self._dependencies.remove(ref)
            return
        selected = next(
            (
                current
                for current in self._dependencies
                if isinstance(current, Ref) and current.without_revision() == ref.without_revision()
            ),
            None,
        )
        if selected is None:
            raise NotFound(f"Dependency is not declared: {ref}")
        if ref.revision is not None and ref != selected:
            raise Conflict(f"Declared dependency is {selected}; requested {ref}")
        self._dependencies.remove(selected)

    def save(self) -> None:
        """Save working metadata and dependencies; SCM stays a flow policy outside YAML."""
        document = self._manifest.model_dump(mode="json", exclude_unset=True)
        configured = [item for item in document.get("dependencies", ()) if isinstance(item, dict)]
        document.update(
            ref=str(self.record.ref),
            dependencies=[*map(str, self.dependencies), *configured],
            files={
                name: os.path.relpath(path.expanduser().absolute(), self.path.parent)
                for name, path in self.record.files.items()
            },
            payload=json.loads(self.record.to_json())["payload"],
        )
        content = yaml.safe_dump(
            {key: value for key, value in document.items() if value is not None}, sort_keys=False
        )
        self.path.write_text(content, encoding="utf-8")


__all__ = ["ConfiguredDependency", "Manifest", "ManifestFile"]
