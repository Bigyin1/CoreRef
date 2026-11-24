"""Editable manifest document shared by CLI commands and test helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import yaml  # type: ignore[import-untyped]

from ..api.errors import Conflict, InvalidPackage, NotFound
from ..api.packages.records import Dependency, Ref, Requirement, _parse_dependency
from ..api.projects.graph import Graph
from ..api.projects.manifest import Manifest
from .recipe import LocalRecipe

if TYPE_CHECKING:
    from ..api.packages.catalog import Catalog
    from ..api.packages.records import PackageInput
    from ..api.projects.workspace import Workspace


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
        return catalog._variants.create_lockfile(
            self.record,
            requirements=self.dependencies,
            inputs=configured.values(),
            path=self.lockfile,
        )

    def add(self, value: Dependency | str) -> None:
        """Add or replace a dependency declaration; changes are saved explicitly."""
        ref = _parse_dependency(value) if isinstance(value, str) else value
        if ref.package_name == self.record.ref.package_name:
            raise InvalidPackage("A package cannot depend on itself")
        for index, current in enumerate(self._dependencies):
            if current.package_name == ref.package_name:
                self._dependencies[index] = ref
                return
        self._dependencies.append(ref)

    def remove(self, value: Dependency | str) -> None:
        """Remove a matching range or logical Ref; an explicit revision must match."""
        ref = _parse_dependency(value) if isinstance(value, str) else value
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
