"""Exact search by the common reference fields."""

from collections.abc import Iterable
from dataclasses import dataclass

from .._internal.references import COMPONENT_PART, NAME_PART, REVISION, VERSION
from .records import LocalRecord, Record, Ref


@dataclass(frozen=True)
class SearchQuery:
    """Exact filters; subcomponent="*" includes parts, None selects whole components."""

    namespace: str | None = None
    component: str | None = None
    view: str | None = None
    type: str | None = None
    version: str | None = None
    subcomponent: str | None = "*"
    revision: str | None = None

    def conan_pattern(self, *, all_revisions: bool = False) -> str | None:
        """Narrow the Conan search; matches_ref applies exact filters to the results."""
        patterns = {"component": COMPONENT_PART, "version": VERSION, "revision": REVISION}
        if any(
            value is not None and not patterns.get(field, NAME_PART).fullmatch(value)
            for field, value in self.criteria().items()
        ):
            return None
        fields = (self.namespace, self._component_pattern, self.view, self.type)
        name = ".".join(value if value is not None else "*" for value in fields)
        revision = self.revision or ("*" if all_revisions else "latest")
        return f"{name}/{self.version if self.version is not None else '*'}#{revision}"

    def criteria(self) -> dict[str, str | None]:
        """Return the specified exact filters."""
        values = {
            "namespace": self.namespace,
            "component": self.component,
            "view": self.view,
            "type": self.type,
            "version": self.version,
            "revision": self.revision,
        }
        criteria: dict[str, str | None] = {
            name: value for name, value in values.items() if value is not None
        }
        if self.subcomponent != "*":
            criteria["subcomponent"] = self.subcomponent
        return criteria

    def matches(self, record: LocalRecord | Record) -> bool:
        """Match common fields without interpreting consumer JSON."""
        return self.matches_ref(record.ref)

    def matches_ref(self, ref: Ref) -> bool:
        """Check logical coordinates before downloading a matching recipe."""
        return all(getattr(ref, name) == value for name, value in self.criteria().items())

    @property
    def _component_pattern(self) -> str:
        if self.subcomponent is None:
            return self.component or "*"
        if self.subcomponent != "*":
            return f"{self.component or '*'}_{self.subcomponent}"
        if self.component is None:
            return "*"
        return f"{self.component}*"

    def select[T: LocalRecord | Record](self, records: Iterable[T]) -> tuple[T, ...]:
        """Select matching records in Ref order without changing the input collection."""
        return tuple(
            sorted((record for record in records if self.matches(record)), key=lambda r: r.ref)
        )


__all__ = ["SearchQuery"]
