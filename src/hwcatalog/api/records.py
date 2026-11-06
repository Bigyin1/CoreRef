"""Exact references and records with consumer-owned JSON payloads."""

import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Self

from frozendict import deepfreeze

from .errors import CatalogError
from .protocols import JSONSerializable, RefCatalog
from .resources import GitResource, MaterializedPackage
from .types import JSON

if TYPE_CHECKING:
    from .query import SearchQuery


@dataclass(frozen=True)
class Ref:
    """Package coordinates with an optional catalog excluded from identity and JSON."""

    namespace: str
    component: str
    view: str
    type: str
    version: str
    catalog: RefCatalog | None = field(default=None, kw_only=True, repr=False, compare=False)

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

    def _context(self) -> RefCatalog:
        if self.catalog is None:
            raise CatalogError("Ref has no catalog; pass catalog=... when creating it")
        return self.catalog

    def get(self) -> Record:
        """Read the published record at these exact coordinates."""
        from .._internal.conan import records

        catalog = self._context()
        return records.get(catalog._client, self)

    def dependencies(self, filters: SearchQuery | None = None) -> tuple[Record, ...]:
        """Read immediate dependencies, optionally filtered by their coordinates."""
        from .._internal.conan import records
        from .query import SearchQuery

        catalog = self._context()
        query = SearchQuery() if filters is None else filters
        return records.dependencies(catalog._client, self, query)

    def find_dependents(
        self, *, candidates: SearchQuery, transitive: bool = True
    ) -> tuple[Record, ...]:
        """Find candidate packages requiring this Ref, directly or transitively."""
        from .._internal.conan import graph

        catalog = self._context()
        return graph.find_dependents(
            catalog._client,
            self,
            candidates=candidates,
            search=catalog.search,
            transitive=transitive,
        )

    def materialize(self, *, with_sources: bool = False) -> MaterializedPackage:
        """Fetch package artifacts and optionally its pinned SCM sources."""
        from .._internal.conan import materialization

        return materialization.materialize(self._context()._client, self, with_sources=with_sources)


@dataclass(frozen=True)
class Record:
    """A consumer-owned payload attached to an exact reference."""

    ref: Ref
    payload: JSON | JSONSerializable = None
    source: GitResource | None = None
    files: tuple[str, ...] = ()

    def to_json(self) -> str:
        """Store the payload's JSON value, without its Python class information."""
        payload = self.payload
        if isinstance(payload, JSONSerializable):
            payload = json.loads(payload.to_json())
        value = {"ref": self.ref.to_dict(), "payload": payload}
        if self.source is not None:
            value["source"] = asdict(self.source)
        if self.files:
            value["files"] = self.files
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def from_json(cls, payload: str, *, catalog: RefCatalog | None = None) -> Self:
        """Restore an immutable JSON snapshot; the consumer decodes its own payload."""
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("Record must be a JSON object")
        source = GitResource(**value["source"]) if value.get("source") is not None else None
        return cls(
            Ref(**value["ref"], catalog=catalog),
            deepfreeze(value["payload"]),
            source,
            tuple(value.get("files", ())),
        )


__all__ = ["Record", "Ref"]
