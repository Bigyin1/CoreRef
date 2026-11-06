"""Exact search by the common reference fields."""

from dataclasses import dataclass

from .._internal.validation import checked_text
from .records import Record


@dataclass(frozen=True)
class SearchQuery:
    namespace: str | None = None
    component: str | None = None
    view: str | None = None
    type: str | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        for name, value in self.criteria().items():
            object.__setattr__(self, name, checked_text(value, blank=True))

    def criteria(self) -> dict[str, str]:
        """Return the specified exact filters."""
        values = {
            "namespace": self.namespace,
            "component": self.component,
            "view": self.view,
            "type": self.type,
            "version": self.version,
        }
        return {name: value for name, value in values.items() if value is not None}

    def matches(self, record: Record) -> bool:
        """Match common fields without interpreting consumer JSON."""
        return all(getattr(record.ref, name) == value for name, value in self.criteria().items())


__all__ = ["SearchQuery"]
