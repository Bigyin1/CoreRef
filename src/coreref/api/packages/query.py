"""Exact search by the common reference fields."""

from __future__ import annotations

from dataclasses import dataclass

from ..._internal.references import NAME_PART, REVISION, VERSION
from ..errors import InvalidPackage
from .records import LocalRecord, Record, Ref


@dataclass(frozen=True)
class SearchQuery:
    """Exact filters over package coordinates."""

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
        """Match common fields without interpreting consumer JSON."""
        return self.matches_ref(record.ref)

    def matches_ref(self, ref: Ref) -> bool:
        """Check logical coordinates before downloading a matching recipe."""
        return all(getattr(ref, name) == value for name, value in self._criteria().items())


__all__ = ["SearchQuery"]
