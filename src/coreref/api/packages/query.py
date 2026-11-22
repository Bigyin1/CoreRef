"""Exact coordinate filters for catalog searches and resolved graphs.

A query compares fields of a ``Ref``. It can be constructed and applied
to records without a catalog.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..._internal.references import NAME_PART, REVISION, VERSION
from ..errors import InvalidPackage
from .records import LocalRecord, Record, Ref


@dataclass(frozen=True)
class SearchQuery:
    """Filter by any combination of namespace, component, version and revision.

    Each supplied field is compared literally. Omit a field to allow any
    value. ``SearchQuery()`` is an explicit unrestricted query. Invalid
    coordinate values raise ``InvalidPackage`` at construction.
    """

    namespace: str | None = None
    component: str | None = None
    version: str | None = None
    revision: str | None = None

    def __post_init__(self) -> None:
        patterns = {"version": VERSION, "revision": REVISION}
        for name, value in self._criteria().items():
            if not patterns.get(name, NAME_PART).fullmatch(value):
                raise InvalidPackage(f"Invalid search field {name}: {value!r}")

    def _conan_pattern(self, *, all_revisions: bool = False) -> str:
        """Narrow the Conan search; matches_ref applies exact filters to the results."""
        name = f"{self.namespace or '*'}.{self.component or '*'}"
        revision = self.revision or ("*" if all_revisions else "latest")
        return f"{name}/{self.version or '*'}#{revision}"

    def _criteria(self) -> dict[str, str]:
        """Return the specified exact filters."""
        values = {
            "namespace": self.namespace,
            "component": self.component,
            "version": self.version,
            "revision": self.revision,
        }
        return {name: value for name, value in values.items() if value is not None}

    def matches(self, record: LocalRecord | Record) -> bool:
        """Check whether the record's reference matches every supplied field."""
        return self.matches_ref(record.ref)

    def matches_ref(self, ref: Ref) -> bool:
        """Compare a reference with the query's exact fields in memory."""
        return all(getattr(ref, name) == value for name, value in self._criteria().items())


__all__ = ["SearchQuery"]
