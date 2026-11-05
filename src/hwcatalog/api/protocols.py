"""Consumer-owned payload serialization contract."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class JSONSerializable(Protocol):
    """A consumer-owned payload that can serialize itself to a JSON document."""

    def to_json(self) -> str: ...


__all__ = ["JSONSerializable"]
