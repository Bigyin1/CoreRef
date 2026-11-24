"""Coordinate and metadata values shared by the catalog and flows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias

from frozendict import frozendict

from ..._internal.references import parse_package_name, validate_version
from ..errors import InvalidPackage

JSON: TypeAlias = (
    bool | int | float | str | Mapping[str, "JSON"] | list["JSON"] | tuple["JSON", ...] | None
)


class Ref:
    """Name a component version as ``namespace.component/version``."""

    def __init__(self, reference: str) -> None:
        """Parse the canonical coordinates of a component."""
        try:
            name, version = reference.split("/")
            namespace, component = parse_package_name(name)
            validate_version(version)
        except (ValueError, InvalidPackage) as error:
            raise InvalidPackage(
                f"Expected namespace.component/version, got {reference!r}"
            ) from error
        self._coordinates = (namespace, component, version)

    @property
    def namespace(self) -> str:
        """Return the namespace of the component."""
        return self._coordinates[0]

    @property
    def component(self) -> str:
        """Return the component name."""
        return self._coordinates[1]

    @property
    def version(self) -> str:
        """Return the declared component version."""
        return self._coordinates[2]

    @property
    def package_name(self) -> str:
        """Return ``namespace.component``."""
        return f"{self.namespace}.{self.component}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Ref):
            return NotImplemented
        return self._coordinates == other._coordinates

    def __hash__(self) -> int:
        return hash(self._coordinates)

    def __str__(self) -> str:
        return f"{self.package_name}/{self.version}"

    def __repr__(self) -> str:
        return f"Ref({str(self)!r})"


@dataclass(frozen=True)
class Record:
    """Describe a component and its selected source and named outputs.

    The catalog and graph return snapshots of the metadata. Use the graph
    to obtain working sources, including any configured editable checkout.
    Use ``Catalog.get_outputs()`` to retrieve the selected result files.
    """

    ref: Ref
    source: Path
    kind: str | None = None
    outputs: Mapping[str, Path] = field(default_factory=frozendict)
    payload: JSON = None
    commit: str | None = None
