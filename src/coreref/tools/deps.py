"""Optional standard YAML input for a local package and its direct dependencies."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, JsonValue, PrivateAttr, ValidationError

from ..api.catalog import Catalog
from ..api.errors import Conflict, InvalidPackage, NotFound
from ..api.graph import Graph
from ..api.records import JSON, LocalRecord, Ref


class DepsFile(BaseModel):
    """Read and edit deps.yaml; custom flow formats can still use LocalRecord and Graph."""

    model_config = ConfigDict(extra="forbid", strict=True)

    reference: str = Field(alias="ref")
    requirements: list[str] = Field(default_factory=list, alias="dependencies")
    source: str | None = None
    files: dict[str, str] = Field(default_factory=dict)
    payload: JsonValue = None
    _catalog: Catalog = PrivateAttr()
    _file: Path = PrivateAttr()

    def __init__(self, catalog: Catalog, path: str | Path = "deps.yaml") -> None:
        filename = Path(path).expanduser().resolve()
        if filename.suffix.lower() not in {".yaml", ".yml"}:
            raise InvalidPackage("Dependency file must use a .yaml or .yml extension")
        try:
            super().__init__(**yaml.safe_load(filename.read_text()))
        except (yaml.YAMLError, ValidationError, TypeError) as error:
            raise InvalidPackage(f"Invalid dependency file {filename}: {error}") from error
        self._catalog = catalog
        self._file = filename
        _ = self.lockfile
        root = Ref(self.reference, catalog=catalog)
        if root.revision is not None:
            raise InvalidPackage("A local package root must not specify a revision")
        names = [ref.package_name for ref in self.dependencies]
        if len(names) != len(set(names)):
            raise InvalidPackage("Duplicate package in dependencies")
        if root.package_name in names:
            raise InvalidPackage("A package cannot depend on itself")

    @classmethod
    def create(
        cls,
        catalog: Catalog,
        ref: Ref,
        path: str | Path = "deps.yaml",
        *,
        source: str | Path | None = None,
    ) -> DepsFile:
        """Create a minimal document exclusively, preserving any existing file."""
        if ref.revision is not None:
            raise InvalidPackage("A local package root must not specify a revision")
        destination = Path(path).expanduser().resolve()
        if destination.suffix.lower() not in {".yaml", ".yml"}:
            raise InvalidPackage("Dependency file must use a .yaml or .yml extension")
        # Coordinates are already validated by Ref; the remaining fields use model defaults.
        document = cls.model_construct(
            reference=str(ref), source=str(source) if source is not None else None
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = yaml.safe_dump(
            document.model_dump(exclude_none=True, by_alias=True), sort_keys=False
        )
        try:
            with destination.open("x", encoding="utf-8") as stream:
                stream.write(content)
        except FileExistsError as error:
            raise Conflict(f"Dependency file already exists: {destination}") from error
        return cls(catalog, destination)

    @property
    def path(self) -> Path:
        return self._file

    @property
    def record(self) -> LocalRecord:
        """Build a detached local input; all paths are relative to the YAML directory."""
        return LocalRecord(
            Ref(self.reference, catalog=self._catalog),
            cast(JSON, deepcopy(self.payload)),
            source=self._path(self.source) if self.source is not None else None,
            files={name: self._path(path) for name, path in self.files.items()},
        )

    @property
    def dependencies(self) -> tuple[Ref, ...]:
        return tuple(Ref(value, catalog=self._catalog) for value in self.requirements)

    @property
    def lockfile(self) -> Path:
        """Each manifest has its own lockfile: deps.yaml -> deps.lock."""
        lockfile = self.path.with_suffix(".lock")
        if lockfile.resolve() == self.path or (
            lockfile.exists() and self.path.exists() and lockfile.samefile(self.path)
        ):
            raise InvalidPackage("Manifest and lockfile must be different files")
        return lockfile

    def resolve(self, *, update: bool = False) -> Graph:
        """Resolve a snapshot; update ignores the old lock but never writes it."""
        return Graph(
            self._catalog,
            self.record,
            dependencies=self.dependencies,
            lockfile=self.lockfile if self.lockfile.exists() and not update else None,
        )

    def create_lockfile(self) -> Path:
        """Resolve current declarations afresh, then save their exact revision selection."""
        return self.resolve(update=True).create_lockfile(self.lockfile)

    def add(self, ref: Ref) -> None:
        """Add or replace one package's requirement; changes are saved explicitly."""
        if ref.package_name == Ref(self.reference, catalog=self._catalog).package_name:
            raise InvalidPackage("A package cannot depend on itself")
        values = [
            str(ref) if current.package_name == ref.package_name else str(current)
            for current in self.dependencies
        ]
        if str(ref) not in values:
            values.append(str(ref))
        self.requirements = values

    def remove(self, ref: Ref) -> None:
        """Remove logical coordinates; an explicit revision must match the declaration."""
        selected = next(
            (r for r in self.dependencies if r.without_revision() == ref.without_revision()), None
        )
        if selected is None:
            raise NotFound(f"Dependency is not declared: {ref}")
        if ref.revision is not None and ref != selected:
            raise Conflict(f"Declared dependency is {selected}; requested {ref}")
        self.requirements.remove(str(selected))

    def save(self) -> None:
        """Write canonical YAML; comments and presentation are not preserved."""
        content = yaml.safe_dump(self.model_dump(exclude_none=True, by_alias=True), sort_keys=False)
        self.path.write_text(content, encoding="utf-8")

    def _path(self, value: str) -> Path:
        return (self.path.parent / Path(value).expanduser()).resolve()


__all__ = ["DepsFile"]
