"""Exact references and records with consumer-owned JSON payloads."""

import json
from collections.abc import Mapping
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Self

from frozendict import deepfreeze, frozendict

from .protocols import JSONSerializable, RefCatalog
from .types import JSON

if TYPE_CHECKING:
    from .query import SearchQuery


@dataclass(frozen=True)
class Ref:
    """Package coordinates with a required private catalog excluded from identity and JSON."""

    namespace: str
    component: str
    view: str
    type: str
    version: str
    catalog: InitVar[RefCatalog] = field(kw_only=True)
    _catalog: RefCatalog = field(init=False, repr=False, compare=False)

    def __post_init__(self, catalog: RefCatalog) -> None:
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

    def get(self, *, with_sources: bool = False) -> Record:
        """Read the published record at these exact coordinates."""
        from .._internal.conan import records

        catalog = self._catalog
        return records.get(catalog._client, self, with_sources=with_sources)

    def dependencies(self, filters: SearchQuery | None = None) -> tuple[Record, ...]:
        """Read immediate dependencies, optionally filtered by their coordinates."""
        from .._internal.conan import records
        from .query import SearchQuery

        catalog = self._catalog
        query = SearchQuery() if filters is None else filters
        return records.dependencies(catalog._client, self, query)

    def find_dependents(
        self, *, candidates: SearchQuery, transitive: bool = True
    ) -> tuple[Record, ...]:
        """Find candidate packages requiring this Ref, directly or transitively."""
        from .._internal.conan import graph

        catalog = self._catalog
        return graph.find_dependents(
            catalog._client,
            self,
            candidates=candidates,
            search=catalog.search,
            transitive=transitive,
        )


@dataclass(frozen=True)
class LocalRecord:
    """Project inputs with already available source, file and NFS paths."""

    ref: Ref
    payload: JSON | JSONSerializable = None
    source: Path | None = None
    files: Mapping[str, Path] = field(default_factory=dict)
    nfs: Mapping[str, Path] = field(default_factory=dict)

    def to_json(self) -> str:
        """Prepare portable package metadata, excluding local source and artifact paths."""
        payload = self.payload
        if isinstance(payload, JSONSerializable):
            payload = json.loads(payload.to_json())
        value: dict[str, JSON] = {"ref": self.ref.to_dict(), "payload": payload}
        if self.files:
            value["files"] = tuple(sorted(self.files))
        if self.nfs:
            value["nfs"] = {name: str(path) for name, path in self.nfs.items()}
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class Record:
    """A downloaded package view with local artifact paths and optional fetched source."""

    ref: Ref
    payload: JSON | JSONSerializable = None
    source: Path | None = field(default=None, compare=False)
    files: Mapping[str, Path] = field(default_factory=dict, compare=False)
    nfs: Mapping[str, Path] = field(default_factory=dict)
    scm: Mapping[str, str] | None = field(default=None, kw_only=True)

    def to_dict(self) -> dict[str, JSON]:
        """Expose downloaded metadata and paths, for inspection or CLI output."""
        payload = self.payload
        if isinstance(payload, JSONSerializable):
            payload = json.loads(payload.to_json())
        value: dict[str, JSON] = {"ref": self.ref.to_dict(), "payload": payload}
        if self.source is not None:
            value["source"] = str(self.source)
        if self.scm is not None:
            value["scm"] = dict(self.scm)
        if self.files:
            value["files"] = {name: str(path) for name, path in self.files.items()}
        if self.nfs:
            value["nfs"] = {name: str(path) for name, path in self.nfs.items()}
        return value

    @classmethod
    def from_json(
        cls, payload: str, *, catalog: RefCatalog, package_dir: Path | None = None
    ) -> Self:
        """Read a package's metadata or an inspection snapshot and attach its local paths."""
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("Record must be a JSON object")
        if package_dir is not None and any(
            name in value for name in ("source", "source_path", "scm")
        ):
            raise ValueError("Git source must be stored in Conan SCM metadata")
        return cls(
            ref=Ref(**value["ref"], catalog=catalog),
            payload=deepfreeze(value["payload"]),
            source=Path(value["source"]) if value.get("source") else None,
            scm=deepfreeze(value.get("scm")),
            files=cls._file_paths(value.get("files", {}), package_dir),
            nfs=deepfreeze({name: Path(path) for name, path in value.get("nfs", {}).items()}),
        )

    @staticmethod
    def _file_paths(value: object, package_dir: Path | None) -> Mapping[str, Path]:
        if isinstance(value, list):
            if value and package_dir is None:
                raise ValueError("Package directory is required for stored file names")
            return frozendict[str, Path](
                {
                    name: package_dir / "artifacts" / name
                    for name in value
                    if package_dir is not None
                }
            )
        if not isinstance(value, dict):
            raise ValueError("Record files must be a path dictionary or stored name list")
        return frozendict[str, Path]({name: Path(path) for name, path in value.items()})


__all__ = ["LocalRecord", "Record", "Ref"]
