"""Exact references and records with consumer-owned JSON payloads."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

from frozendict import deepfreeze, frozendict

if TYPE_CHECKING:
    from .catalog import Catalog
    from .query import SearchQuery


type JSON = bool | int | float | str | Mapping[str, JSON] | list[JSON] | tuple[JSON, ...] | None


@runtime_checkable
class JSONSerializable(Protocol):
    """A consumer-owned payload that can serialize itself to a JSON document."""

    def to_json(self) -> str: ...


class Ref:
    """Package coordinates with a required private catalog excluded from identity and JSON."""

    def __init__(  # noqa: PLR0913, PLR0917 - One argument per coordinate and context.
        self,
        namespace: str,
        component: str,
        view: str,
        type: str,
        version: str,
        *,
        subcomponent: str | None = None,
        revision: str | None = None,
        catalog: Catalog,
    ) -> None:
        if catalog is None:
            raise TypeError("Ref requires a catalog")
        self._namespace = namespace
        self._component = component
        self._view = view
        self._type = type
        self._version = version
        self._subcomponent = subcomponent
        self._revision = revision
        self._catalog = catalog

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def component(self) -> str:
        return self._component

    @property
    def view(self) -> str:
        return self._view

    @property
    def type(self) -> str:
        return self._type

    @property
    def version(self) -> str:
        return self._version

    @property
    def subcomponent(self) -> str | None:
        return self._subcomponent

    @property
    def revision(self) -> str | None:
        return self._revision

    @property
    def _identity(self) -> tuple[str, str, str, str, str, str | None, str | None]:
        return (
            self.namespace,
            self.component,
            self.view,
            self.type,
            self.version,
            self.subcomponent,
            self.revision,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Ref):
            return NotImplemented
        return self._identity == other._identity

    def __hash__(self) -> int:
        return hash(self._identity)

    def __repr__(self) -> str:
        coordinates = ", ".join(f"{name}={value!r}" for name, value in self.to_dict().items())
        return f"Ref({coordinates})"

    @property
    def package_name(self) -> str:
        """The namespace, component and representation without a version."""
        component = self.component
        if self.subcomponent is not None:
            component = f"{component}_{self.subcomponent}"
        return f"{self.namespace}.{component}.{self.view}.{self.type}"

    def __str__(self) -> str:
        value = f"{self.package_name}/{self.version}"
        return f"{value}#{self.revision}" if self.revision is not None else value

    def __lt__(self, other: Ref) -> bool:
        if not isinstance(other, Ref):
            return NotImplemented
        return (
            self.namespace,
            self.component,
            self.subcomponent or "",
            self.view,
            self.type,
            self.version,
            self.revision or "",
        ) < (
            other.namespace,
            other.component,
            other.subcomponent or "",
            other.view,
            other.type,
            other.version,
            other.revision or "",
        )

    def to_dict(self) -> dict[str, str]:
        """Serialize package coordinates without the runtime catalog context."""
        coordinates = {
            "namespace": self.namespace,
            "component": self.component,
            "view": self.view,
            "type": self.type,
            "version": self.version,
        }
        if self.subcomponent is not None:
            coordinates["subcomponent"] = self.subcomponent
        if self.revision is not None:
            coordinates["revision"] = self.revision
        return coordinates

    def without_revision(self) -> Ref:
        """Return logical coordinates bound to the same catalog."""
        coordinates = self.to_dict()
        coordinates.pop("revision", None)
        return Ref(**coordinates, catalog=self._catalog)

    def info(self) -> Record:
        """Read metadata without installing package files or Git sources."""
        return self._catalog._package(self).info()

    def get(self) -> InstalledRecord:
        """Get exact metadata and files; an unpinned Ref selects the latest revision."""
        return self._catalog._package(self).get()

    def get_with_sources(self) -> InstalledRecord:
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

    _format: ClassVar[int] = 7
    ref: Ref
    payload: JSON | JSONSerializable = None
    source: Path | None = None
    files: Mapping[str, Path] = field(default_factory=dict)

    def to_json(self) -> str:
        """Prepare portable package metadata, excluding local source and artifact paths."""
        payload = self.payload
        if isinstance(payload, JSONSerializable):
            payload = json.loads(payload.to_json())
        value: dict[str, JSON] = {
            "format": self._format,
            "ref": self.ref.without_revision().to_dict(),
            "payload": payload,
        }
        if self.files:
            value["files"] = tuple(sorted(self.files))
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Record:
    """Immutable package metadata, without paths to installed contents."""

    def __init__(
        self,
        ref: Ref,
        payload: JSON | JSONSerializable = None,
        *,
        file_names: tuple[str, ...] = (),
        has_source: bool = False,
    ) -> None:
        if isinstance(payload, JSONSerializable):
            payload = json.loads(payload.to_json())
        self._ref = ref
        self._payload: JSON = deepfreeze(json.loads(json.dumps(payload, allow_nan=False)))
        self._file_names = tuple(sorted(file_names))
        self._has_source = has_source

    @property
    def ref(self) -> Ref:
        return self._ref

    @property
    def payload(self) -> JSON:
        return self._payload

    @property
    def file_names(self) -> tuple[str, ...]:
        return self._file_names

    @property
    def has_source(self) -> bool:
        return self._has_source

    def __repr__(self) -> str:
        return f"{type(self).__name__}(ref={self.ref!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Record):
            return NotImplemented
        return (self.ref, self.payload, self.file_names, self.has_source) == (
            other.ref,
            other.payload,
            other.file_names,
            other.has_source,
        )

    def __hash__(self) -> int:
        return hash((self.ref, self.payload, self.file_names, self.has_source))

    def to_dict(self) -> dict[str, JSON]:
        """Expose metadata for inspection or CLI output."""
        return {
            "ref": self.ref.to_dict(),
            "payload": self.payload,
            "file_names": self.file_names,
            "has_source": self.has_source,
        }

    @staticmethod
    def from_json(payload: str, *, catalog: Catalog) -> Record:
        """Restore a metadata or installed snapshot, according to its fields."""
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("Record must be a JSON object")
        if "scm" in value:
            raise ValueError("Git source must be stored in Conan SCM metadata")
        record = Record(
            Ref(**value["ref"], catalog=catalog),
            value["payload"],
            file_names=tuple(value.get("file_names", ())),
            has_source=value.get("has_source", False),
        )
        if "files" not in value:
            if "source" in value:
                raise ValueError("An installed snapshot must include its files mapping")
            return record
        files = value["files"]
        if not isinstance(files, dict):
            raise ValueError("Snapshot files must be a path dictionary")
        return InstalledRecord(
            record,
            source=Path(value["source"]) if value.get("source") else None,
            files={name: Path(path) for name, path in files.items()},
        )


class InstalledRecord(Record):
    """Package metadata with all packaged files available and optional fetched sources."""

    def __init__(
        self,
        record: Record,
        *,
        files: Mapping[str, Path] | None = None,
        source: Path | None = None,
    ) -> None:
        # Metadata is already detached and immutable; reuse it without serializing again.
        self._ref = record.ref
        self._payload = record.payload
        self._file_names = record.file_names
        self._has_source = record.has_source
        self._files = frozendict(files or {})
        self._source = source
        if set(self.file_names) != set(self.files):
            raise ValueError("Installed files must match the declared file names")
        if source is not None and not self.has_source:
            raise ValueError("The package declares no SCM source")

    @property
    def source(self) -> Path | None:
        return self._source

    @property
    def files(self) -> Mapping[str, Path]:
        return self._files

    @property
    def sources_ready(self) -> bool:
        """Whether sources are available or the package declares none."""
        return not self.has_source or self.source is not None

    def to_dict(self) -> dict[str, JSON]:
        """Include paths only in installed inspection snapshots."""
        value = super().to_dict()
        value["files"] = {name: str(path) for name, path in self.files.items()}
        if self.source is not None:
            value["source"] = str(self.source)
        return value


__all__ = ["JSON", "InstalledRecord", "JSONSerializable", "LocalRecord", "Record", "Ref"]
