"""Payload serialization and the catalog context required by package references."""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .._internal.conan.client import ConanClient
    from .models.query import SearchQuery
    from .models.records import Record


@runtime_checkable
class JSONSerializable(Protocol):
    """A consumer-owned payload that can serialize itself to a JSON document."""

    def to_json(self) -> str: ...


class RefCatalog(Protocol):
    """Search access and the internal Conan context used by package references."""

    _client: ConanClient

    def search(self, query: SearchQuery) -> tuple[Record, ...]: ...


__all__ = ["JSONSerializable", "RefCatalog"]
