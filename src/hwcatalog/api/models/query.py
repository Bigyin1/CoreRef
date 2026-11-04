"""Exact search by the common reference fields."""

from dataclasses import dataclass

from .records import Record


@dataclass(frozen=True)
class SearchQuery:
    name: str | None = None
    version: str | None = None
    kind: str | None = None
    namespace: str | None = None

    def criteria(self) -> dict[str, str]:
        """Return the specified exact filters."""
        values = {
            "name": self.name,
            "version": self.version,
            "kind": self.kind,
            "namespace": self.namespace,
        }
        return {name: value for name, value in values.items() if value is not None}

    def matches(self, record: Record) -> bool:
        """Match common fields without interpreting consumer JSON."""
        return all(getattr(record.ref, name) == value for name, value in self.criteria().items())


__all__ = ["SearchQuery"]
