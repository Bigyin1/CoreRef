"""Optional standard YAML input for a local package and its direct dependencies."""

from copy import deepcopy
from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from ..api.catalog import Catalog
from ..api.errors import Conflict, InvalidPackage, NotFound
from ..api.graph import Graph
from ..api.records import JSON, LocalRecord, Ref
from ..api.workspace import Workspace


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ref: str
    dependencies: list[str] = Field(default_factory=list)
    source: str | None = None
    files: dict[str, str] = Field(default_factory=dict)
    payload: JsonValue = None


class DepsFile:
    """Read and edit deps.yaml; custom flow formats can still use LocalRecord and Graph."""

    def __init__(self, catalog: Catalog, path: str | Path = "deps.yaml") -> None:
        self._catalog = catalog
        self.path = Path(path).expanduser().resolve()
        try:
            self._data = _Document.model_validate(yaml.safe_load(self.path.read_text()))
        except (yaml.YAMLError, ValidationError) as error:
            raise InvalidPackage(f"Invalid dependency file {self.path}: {error}") from error
        root = Ref(self._data.ref, catalog=catalog)
        if root.revision is not None:
            raise InvalidPackage("A local package root must not specify a revision")
        # Validate coordinate spelling without resolving or downloading anything.
        _ = self.dependencies

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
        document = _Document(ref=str(ref), source=str(source) if source is not None else None)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("x") as stream:
                yaml.safe_dump(document.model_dump(exclude_none=True), stream, sort_keys=False)
        except FileExistsError as error:
            raise Conflict(f"Dependency file already exists: {destination}") from error
        return cls(catalog, destination)

    @property
    def record(self) -> LocalRecord:
        """Build a detached local input; all paths are relative to the YAML directory."""
        return LocalRecord(
            Ref(self._data.ref, catalog=self._catalog),
            cast(JSON, deepcopy(self._data.payload)),
            source=self._path(self._data.source) if self._data.source is not None else None,
            files={name: self._path(path) for name, path in self._data.files.items()},
        )

    @property
    def dependencies(self) -> tuple[Ref, ...]:
        return tuple(Ref(value, catalog=self._catalog) for value in self._data.dependencies)

    @property
    def lockfile(self) -> Path:
        """Each manifest has its own lockfile: deps.yaml -> deps.lock."""
        return self.path.with_suffix(".lock")

    def resolve(self, *, update: bool = False) -> Graph:
        """Resolve a snapshot; update ignores the old lock but never writes it."""
        workspace = Workspace(self._catalog, self.path.parent / "conanws.yml")
        return Graph(
            self._catalog,
            self.record,
            dependencies=self.dependencies,
            lockfile=self.lockfile if self.lockfile.exists() and not update else None,
            workspace=workspace if workspace.path.exists() else None,
        )

    def create_lockfile(self) -> Path:
        """Resolve current declarations afresh, then save their exact revision selection."""
        return self.resolve(update=True).create_lockfile(self.lockfile)

    def add(self, ref: Ref) -> None:
        """Add or replace one package's requirement; changes are saved explicitly."""
        if ref.package_name == Ref(self._data.ref, catalog=self._catalog).package_name:
            raise InvalidPackage("A package cannot depend on itself")
        values = [
            str(ref) if current.package_name == ref.package_name else str(current)
            for current in self.dependencies
        ]
        if str(ref) not in values:
            values.append(str(ref))
        self._data.dependencies = values

    def remove(self, ref: Ref) -> None:
        """Remove logical coordinates; an explicit revision must match the declaration."""
        selected = next(
            (r for r in self.dependencies if r.without_revision() == ref.without_revision()), None
        )
        if selected is None:
            raise NotFound(f"Dependency is not declared: {ref}")
        if ref.revision is not None and ref != selected:
            raise Conflict(f"Declared dependency is {selected}; requested {ref}")
        self._data.dependencies.remove(str(selected))

    def save(self) -> None:
        """Write canonical YAML; comments and presentation are not preserved."""
        self.path.write_text(
            yaml.safe_dump(self._data.model_dump(exclude_none=True), sort_keys=False)
        )

    def _path(self, value: str) -> Path:
        return (self.path.parent / Path(value).expanduser()).resolve()


__all__ = ["DepsFile"]
