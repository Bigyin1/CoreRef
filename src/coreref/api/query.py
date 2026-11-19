"""Exact search by the common reference fields."""

from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import Self

from .._internal.references import NAME_PART, REVISION, VERSION
from .errors import InvalidPackage
from .records import LocalRecord, Record, Ref


@dataclass(frozen=True)
class SearchQuery:
    """Exact filters over standalone packages and component representations."""

    namespace: str | None = None
    component: str | None = None
    view: str | None = None
    type: str | None = None
    version: str | None = None
    revision: str | None = None

    @classmethod
    def for_component(
        cls,
        ref: Ref,
        *,
        view: str | None = None,
        type: str | None = None,
        version: str | None = None,
        revision: str | None = None,
    ) -> Self:
        """Find representations of exactly this component, with independently selected versions."""
        return cls(
            namespace=ref.namespace,
            component=ref.component,
            view=view,
            type=type,
            version=version,
            revision=revision,
        )

    def __post_init__(self) -> None:
        patterns = {"version": VERSION, "revision": REVISION}
        for name, value in self._criteria().items():
            if not patterns.get(name, NAME_PART).fullmatch(value):
                raise InvalidPackage(f"Invalid search field {name}: {value!r}")

    def _conan_pattern(self, *, all_revisions: bool = False) -> str:
        """Narrow the Conan search; matches_ref applies exact filters to the results."""
        name = self._name_pattern()
        revision = self.revision or ("*" if all_revisions else "latest")
        return f"{name}/{self.version if self.version is not None else '*'}#{revision}"

    def _name_pattern(self) -> str:
        namespace = self.namespace or "*"
        component = self.component or "*"
        if self.view is not None or self.type is not None:
            return f"{namespace}.{component}.{self.view or '*'}.{self.type or '*'}"
        # A single pattern covers both name lengths. Exact fields are checked after listing.
        return f"{namespace}.{component}*" if self.component else f"{namespace}.*"

    def _criteria(self) -> dict[str, str]:
        """Return the specified exact filters."""
        values = {
            "namespace": self.namespace,
            "component": self.component,
            "view": self.view,
            "type": self.type,
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
