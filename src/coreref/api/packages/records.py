"""Package coordinates, requirements and records exchanged through the SDK.

``LocalRecord`` is a mutable input for a package under development.
``Record`` describes a resolved package, and ``InstalledRecord`` adds
filesystem paths. ``ConfiguredPackage`` tracks the binary identity
selected for a particular set of options.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Protocol, TypeAlias, runtime_checkable

from frozendict import deepfreeze, frozendict

JSON: TypeAlias = (
    bool | int | float | str | Mapping[str, "JSON"] | list["JSON"] | tuple["JSON", ...] | None
)


@runtime_checkable
class _JSONSerializable(Protocol):
    """A consumer-owned payload that can serialize itself to a JSON document."""

    def to_json(self) -> str: ...


class Ref:
    """Identify a version of a component or library.

    Use ``namespace.component/version[#revision]``. The optional suffix
    identifies a locked source or release and participates in equality and
    hashing. A reference contains only coordinates. It can be shared
    between catalogs. Ordering is lexicographic over the coordinate
    fields and suitable for stable display.
    """

    def __init__(self, reference: str) -> None:
        """Parse canonical coordinates or raise ``InvalidPackage``."""
        from ..._internal.references import parse_package_name, validate_fields
        from ..errors import InvalidPackage

        try:
            logical, _, revision = reference.partition("#")
            name, self._version = logical.split("/")
            self._namespace, self._component = parse_package_name(name)
            self._revision = revision or None
            validate_fields(self._version, self._revision)
            if str(self) != reference:
                raise ValueError("Noncanonical coordinates")
        except (ValueError, InvalidPackage) as error:
            raise InvalidPackage(
                f"Expected namespace.component/version[#revision], got {reference!r}"
            ) from error

    @classmethod
    def from_fields(
        cls,
        namespace: str,
        component: str,
        version: str,
        *,
        revision: str | None = None,
    ) -> Ref:
        """Construct a canonical reference from separately supplied coordinates.

        The keyword-only revision pins a recipe revision when supplied.
        Separator characters belong between fields.
        """
        from ..errors import InvalidPackage

        reference = f"{namespace}.{component}/{version}"
        if revision is not None:
            reference = f"{reference}#{revision}"
        ref = cls(reference)
        if ref._identity != (namespace, component, version, revision):
            raise InvalidPackage("Reference separators must not occur within coordinate fields")
        return ref

    @property
    def namespace(self) -> str:
        """Return the first segment of the package name."""
        return self._namespace

    @property
    def component(self) -> str:
        """Return the component or library name within the namespace."""
        return self._component

    @property
    def version(self) -> str:
        """Return the concrete version string."""
        return self._version

    @property
    def revision(self) -> str | None:
        """Return the exact source or release revision, if specified."""
        return self._revision

    @property
    def _identity(self) -> tuple[str, str, str, str | None]:
        return self.namespace, self.component, self.version, self.revision

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
        """Return ``namespace.component`` without version or revision."""
        return f"{self.namespace}.{self.component}"

    def __str__(self) -> str:
        """Format canonical coordinates for a manifest or command line."""
        value = f"{self.package_name}/{self.version}"
        return f"{value}#{self.revision}" if self.revision is not None else value

    def __lt__(self, other: Ref) -> bool:
        if not isinstance(other, Ref):
            return NotImplemented
        return (
            self.namespace,
            self.component,
            self.version,
            self.revision or "",
        ) < (
            other.namespace,
            other.component,
            other.version,
            other.revision or "",
        )

    def to_dict(self) -> dict[str, str]:
        """Export individual coordinate fields, omitting an unset revision."""
        coordinates = {
            "namespace": self.namespace,
            "component": self.component,
            "version": self.version,
        }
        if self.revision is not None:
            coordinates["revision"] = self.revision
        return coordinates

    def without_revision(self) -> Ref:
        """Return a reference with the same name and version and no revision.

        Use it when you deliberately want a logical reference that the catalog can
        resolve again.
        """
        return Ref(str(self).partition("#")[0])


class Requirement:
    """Declare a version range for a source dependency.

    For example, ``Requirement("shared.stdlib/[>=1.0 <2.0]")`` leaves
    version selection to the catalog while locking. A resolved dependency is identified by a
    ``Ref``. Use a concrete ``Ref`` for the root of a project or for
    individual package retrieval.
    """

    def __init__(self, reference: str) -> None:
        """Validate a package name and a bracketed range expression."""
        from ..._internal.references import parse_package_name, version_range

        name, _, version = reference.partition("/")
        parse_package_name(name)
        self._specifier = version_range(version)
        self._package_name = name
        self._version = version

    @property
    def package_name(self) -> str:
        """Return the package name covered by the range."""
        return self._package_name

    def _matches(self, ref: Ref) -> bool:
        from packaging.version import InvalidVersion

        try:
            return ref.package_name == self.package_name and self._specifier.contains(ref.version)
        except InvalidVersion:
            return False

    def __str__(self) -> str:
        """Format the original version-range requirement."""
        return f"{self.package_name}/{self._version}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Requirement):
            return NotImplemented
        return str(self) == str(other)

    def __hash__(self) -> int:
        return hash(str(self))


Dependency: TypeAlias = Ref | Requirement


def _parse_dependency(text: str) -> Dependency:
    """Read either an exact version or a version range."""
    return Requirement(text) if text.partition("/")[2].startswith("[") else Ref(text)


@dataclass
class LocalRecord:
    """Describe a package before publishing it.

    Use a ``ref`` without a recipe revision. ``source``, when set, is the
    local Git checkout used for SCM. ``files`` maps logical artifact names
    to paths to include in the package. ``payload`` holds flow-owned JSON
    or an object whose ``to_json()`` returns JSON text.
    """

    _format: ClassVar[int] = 7
    ref: Ref
    payload: JSON | _JSONSerializable = None
    source: Path | None = None
    files: dict[str, Path] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize portable metadata for the package recipe.

        The document contains logical coordinates, the payload and declared
        artifact names. Local filesystem paths are supplied separately during
        packaging.
        """
        payload = self.payload
        if isinstance(payload, _JSONSerializable):
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
    """Read-only recipe metadata for a package.

    Read the reference, flow-owned payload, declared file names and source
    availability before deciding what to install. Catalog and graph
    operations supply the selected references for published packages.
    """

    def __init__(
        self,
        ref: Ref,
        payload: JSON | _JSONSerializable = None,
        *,
        file_names: tuple[str, ...] = (),
        has_source: bool = False,
    ) -> None:
        """Create a metadata snapshot with a detached, immutable JSON payload.

        File names are stored in sorted order. ``has_source`` declares source
        availability at the recipe level.
        """
        if isinstance(payload, _JSONSerializable):
            payload = json.loads(payload.to_json())
        self._ref = ref
        self._payload: JSON = deepfreeze(json.loads(json.dumps(payload, allow_nan=False)))
        self._file_names = tuple(sorted(file_names))
        self._has_source = has_source

    @property
    def ref(self) -> Ref:
        """Return the coordinates associated with this metadata."""
        return self._ref

    @property
    def payload(self) -> JSON:
        """Return the frozen JSON value owned by the publishing flow."""
        return self._payload

    @property
    def file_names(self) -> tuple[str, ...]:
        """Return sorted names of files packaged directly with this recipe."""
        return self._file_names

    @property
    def has_source(self) -> bool:
        """Indicate whether the recipe declares a Git source checkout."""
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
        """Return a metadata dictionary for inspection or CLI output."""
        return {
            "ref": self.ref.to_dict(),
            "payload": self.payload,
            "file_names": self.file_names,
            "has_source": self.has_source,
        }


class InstalledRecord(Record):
    """A metadata record with paths to packaged files and optional Git sources.

    Paths refer to the local Nix store or to an editable working
    directory. They describe the local installation at the time of retrieval.
    """

    def __init__(
        self,
        record: Record,
        *,
        files: Mapping[str, Path] | None = None,
        source: Path | None = None,
        is_editable: bool = False,
    ) -> None:
        # Metadata is already detached and immutable; reuse it without serializing again.
        """Associate an installation with existing metadata.

        The file mapping must cover exactly the declared names. Supply a
        source path only for metadata that declares source availability.
        """
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
        """Return the available Git checkout path, if sources were obtained."""
        return self._source

    @property
    def files(self) -> Mapping[str, Path]:
        """Return the read-only mapping of file names to local paths."""
        return self._files

    @property
    def is_editable(self) -> bool:
        """Indicate whether the graph selected an editable checkout."""
        return self._is_editable

    @property
    def _sources_ready(self) -> bool:
        """Whether sources are available or the package declares none."""
        return not self.has_source or self.source is not None

    def to_dict(self) -> dict[str, JSON]:
        """Return metadata and installation paths for local inspection.

        Paths are rendered as strings. The source path is included when present.
        """
        value = super().to_dict()
        value["files"] = {name: str(path) for name, path in self.files.items()}
        value["is_editable"] = self.is_editable
        if self.source is not None:
            value["source"] = str(self.source)
        return value


@dataclass(frozen=True)
class PackageInput:
    """Describe a requirement and its options for binary selection.

    Use either a concrete ``Ref`` or a version-range ``Requirement``.
    Options are copied when this value is constructed. Use an empty
    mapping to select the recipe's default configuration.
    """

    requirement: Dependency
    options: Mapping[str, str | int | bool]

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))


@dataclass(frozen=True)
class ConfiguredPackage:
    """The exact result of building a package with a particular option set."""

    ref: Ref
    directory: Path

    @property
    def binary_reference(self) -> str:
        """Return the independently addressable Nix result directory."""
        return str(self.directory)


__all__ = [
    "JSON",
    "ConfiguredPackage",
    "Dependency",
    "InstalledRecord",
    "LocalRecord",
    "PackageInput",
    "Record",
    "Ref",
    "Requirement",
]
