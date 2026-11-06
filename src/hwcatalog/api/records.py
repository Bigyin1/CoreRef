"""Exact references and records with consumer-owned JSON payloads."""

import json
from collections.abc import Mapping
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Self, cast, runtime_checkable

from frozendict import deepfreeze, frozendict

from .types import JSON

if TYPE_CHECKING:
    from .catalog import Catalog
    from .query import SearchQuery


@runtime_checkable
class JSONSerializable(Protocol):
    """A consumer-owned payload that can serialize itself to a JSON document."""

    def to_json(self) -> str: ...


@dataclass(frozen=True)
class Ref:
    """Package coordinates with a required private catalog excluded from identity and JSON."""

    namespace: str
    component: str
    view: str
    type: str
    version: str
    catalog: InitVar[Catalog] = field(kw_only=True)
    _catalog: Catalog = field(init=False, repr=False, compare=False)

    def __post_init__(self, catalog: Catalog) -> None:
        if catalog is None:
            raise TypeError("Ref requires a catalog")
        object.__setattr__(self, "_catalog", catalog)

    @property
    def package_name(self) -> str:
        """The namespace, component and representation without a version."""
        return f"{self.namespace}.{self.component}.{self.view}.{self.type}"

    def __str__(self) -> str:
        return f"{self.package_name}/{self.version}"

    def __lt__(self, other: Ref) -> bool:
        if not isinstance(other, Ref):
            return NotImplemented
        return (self.namespace, self.component, self.view, self.type, self.version) < (
            other.namespace,
            other.component,
            other.view,
            other.type,
            other.version,
        )

    def to_dict(self) -> dict[str, str]:
        """Serialize package coordinates without the runtime catalog context."""
        return {
            "namespace": self.namespace,
            "component": self.component,
            "view": self.view,
            "type": self.type,
            "version": self.version,
        }

    def get(self) -> Record:
        """Get package metadata and stored files without fetching Git sources."""
        return self._catalog._package(self).get()

    def get_with_sources(self) -> Record:
        """Get package files and its Git checkout when the recipe declares one."""
        return self._catalog._package(self).get_with_sources()

    def dependencies(self, filters: SearchQuery | None = None) -> tuple[Record, ...]:
        """Read immediate dependencies, optionally filtered by their coordinates."""
        from .query import SearchQuery

        query = SearchQuery() if filters is None else filters
        return self._catalog._package(self).dependencies(query)

    def find_dependents(
        self, *, candidates: SearchQuery, transitive: bool = True
    ) -> tuple[Record, ...]:
        """Find candidate packages requiring this Ref, directly or transitively."""
        return self._catalog._package(self).find_dependents(candidates, transitive=transitive)


@dataclass
class LocalRecord:
    """Project metadata with already available source and package file paths."""

    ref: Ref
    payload: JSON | JSONSerializable = None
    source: Path | None = None
    files: Mapping[str, Path] = field(default_factory=dict)

    def to_json(self) -> str:
        """Prepare portable package metadata, excluding local source and artifact paths."""
        payload = self.payload
        if isinstance(payload, JSONSerializable):
            payload = json.loads(payload.to_json())
        value: dict[str, JSON] = {"ref": self.ref.to_dict(), "payload": payload}
        if self.files:
            value["files"] = tuple(sorted(self.files))
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class Record:
    """A downloaded package view with local artifact paths and optional fetched source."""

    ref: Ref
    payload: JSON | JSONSerializable = None
    source: Path | None = field(default=None, compare=False)
    files: Mapping[str, Path] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        payload = self.payload
        if isinstance(payload, JSONSerializable):
            payload = json.loads(payload.to_json())
        object.__setattr__(
            self, "payload", deepfreeze(json.loads(json.dumps(payload, allow_nan=False)))
        )
        object.__setattr__(self, "files", frozendict(self.files))

    def to_dict(self) -> dict[str, JSON]:
        """Expose downloaded metadata and paths, for inspection or CLI output."""
        # __post_init__ has already converted consumer objects into frozen JSON.
        payload = cast(JSON, self.payload)
        value: dict[str, JSON] = {"ref": self.ref.to_dict(), "payload": payload}
        if self.source is not None:
            value["source"] = str(self.source)
        if self.files:
            value["files"] = {name: str(path) for name, path in self.files.items()}
        return value

    @classmethod
    def from_json(cls, payload: str, *, catalog: Catalog) -> Self:
        """Restore an inspection snapshot with explicit local paths."""
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("Record must be a JSON object")
        if "scm" in value:
            raise ValueError("Git source must be stored in Conan SCM metadata")
        files = value.get("files", {})
        if not isinstance(files, dict):
            raise ValueError("Snapshot files must be a path dictionary")
        return cls(
            ref=Ref(**value["ref"], catalog=catalog),
            payload=value["payload"],
            source=Path(value["source"]) if value.get("source") else None,
            files={name: Path(path) for name, path in files.items()},
        )


__all__ = ["JSONSerializable", "LocalRecord", "Record", "Ref"]
