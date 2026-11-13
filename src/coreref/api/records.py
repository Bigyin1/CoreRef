"""Exact references and records with consumer-owned JSON payloads."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Protocol, TypeAlias, runtime_checkable

from frozendict import deepfreeze, frozendict

JSON: TypeAlias = (
    bool | int | float | str | Mapping[str, "JSON"] | list["JSON"] | tuple["JSON", ...] | None
)


@runtime_checkable
class JSONSerializable(Protocol):
    """A consumer-owned payload that can serialize itself to a JSON document."""

    def to_json(self) -> str: ...


class Ref:
    """Package coordinates independent of any client, cache or registry."""

    def __init__(self, reference: str) -> None:
        from .._internal.references import to_conan_reference
        from .errors import InvalidPackage

        try:
            name, self._version = reference.split("#", 1)[0].split("/")
            self._namespace, self._component, *parts, self._view, self._type = name.split(".")
            if len(parts) > 1:
                raise ValueError("Expected four or five name fields")
            self._subcomponent = parts[0] if parts else None
            self._revision = reference.partition("#")[2] or None
            if to_conan_reference(self) != reference:
                raise ValueError("Noncanonical coordinates")
        except (ValueError, InvalidPackage) as error:
            raise InvalidPackage(
                "Expected namespace.component[.subcomponent].view.type/version[#revision], "
                f"got {reference!r}"
            ) from error

    @classmethod
    def from_fields(  # noqa: PLR0913, PLR0917 - One argument per coordinate.
        cls,
        namespace: str,
        component: str,
        view: str,
        type: str,
        version: str,
        *,
        subcomponent: str | None = None,
        revision: str | None = None,
    ) -> Ref:
        """Construct from separate coordinates using the same canonical text parser."""
        from .errors import InvalidPackage

        reference = f"{namespace}.{component}"
        if subcomponent is not None:
            reference = f"{reference}.{subcomponent}"
        reference = f"{reference}.{view}.{type}/{version}"
        if revision is not None:
            reference = f"{reference}#{revision}"
        ref = cls(reference)
        if ref._identity != (namespace, component, view, type, version, subcomponent, revision):
            raise InvalidPackage("Reference separators must not occur within coordinate fields")
        return ref

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
        return f"Ref({str(self)!r})"

    @property
    def package_name(self) -> str:
        """The namespace, component and representation without a version."""
        component = self.component
        if self.subcomponent is not None:
            component = f"{component}.{self.subcomponent}"
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
        """Serialize package coordinates."""
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
        """Return logical coordinates without a recipe revision."""
        return Ref(str(self).partition("#")[0])


@dataclass
class LocalRecord:
    """Mutable metadata and file paths prepared during one CLI command."""

    _format: ClassVar[int] = 7
    ref: Ref
    payload: JSON | JSONSerializable = None
    source: Path | None = None
    files: dict[str, Path] = field(default_factory=dict)

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


class InstalledRecord(Record):
    """Package metadata with all packaged files available and optional fetched sources."""

    def __init__(
        self,
        record: Record,
        *,
        files: Mapping[str, Path] | None = None,
        source: Path | None = None,
        is_editable: bool = False,
    ) -> None:
        # Metadata is already detached and immutable; reuse it without serializing again.
        self._ref = record.ref
        self._payload = record.payload
        self._file_names = record.file_names
        self._has_source = record.has_source
        self._files = frozendict(files or {})
        self._source = source
        self._is_editable = is_editable
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
    def is_editable(self) -> bool:
        """Whether Conan selected a working directory instead of a cached package."""
        return self._is_editable

    @property
    def sources_ready(self) -> bool:
        """Whether sources are available or the package declares none."""
        return not self.has_source or self.source is not None

    def to_dict(self) -> dict[str, JSON]:
        """Include paths only in installed inspection snapshots."""
        value = super().to_dict()
        value["files"] = {name: str(path) for name, path in self.files.items()}
        value["is_editable"] = self.is_editable
        if self.source is not None:
            value["source"] = str(self.source)
        return value


__all__ = ["JSON", "InstalledRecord", "JSONSerializable", "LocalRecord", "Record", "Ref"]
