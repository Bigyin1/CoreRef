"""Exact search by the common reference fields."""

from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property

from .._internal.conan.references import NAME_PART, VERSION
from .records import LocalRecord, Record


@dataclass(frozen=True)
class SearchQuery:
    namespace: str | None = None
    component: str | None = None
    view: str | None = None
    type: str | None = None
    version: str | None = None

    @cached_property
    def conan_pattern(self) -> str | None:
        """Encode exact filters; only unspecified fields become Conan wildcards."""
        fields = (self.namespace, self.component, self.view, self.type)
        if any(value is not None and not NAME_PART.fullmatch(value) for value in fields):
            return None
        if self.version is not None and not VERSION.fullmatch(self.version):
            return None
        name = ".".join(value if value is not None else "*" for value in fields)
        return f"{name}/{self.version if self.version is not None else '*'}#*"

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

    def matches(self, record: LocalRecord | Record) -> bool:
        """Match common fields without interpreting consumer JSON."""
        return all(getattr(record.ref, name) == value for name, value in self.criteria().items())

    def select[T: LocalRecord | Record](self, records: Iterable[T]) -> tuple[T, ...]:
        """Select matching records in Ref order without changing the input collection."""
        return tuple(
            sorted((record for record in records if self.matches(record)), key=lambda r: r.ref)
        )


__all__ = ["SearchQuery"]
