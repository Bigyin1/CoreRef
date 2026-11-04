"""Exact references and records with consumer-owned JSON payloads."""

import json
from dataclasses import asdict, dataclass
from typing import Self

from frozendict import deepfreeze

from ..protocols import JSONSerializable
from ..types import JSON


@dataclass(frozen=True)
class Ref:
    """Exact name/version and consumer-defined kind within a namespace."""

    name: str
    version: str
    kind: str
    namespace: str

    @property
    def identity(self) -> str:
        """An unambiguous key for these exact coordinates."""
        return json.dumps(
            [self.namespace, self.name, self.version, self.kind],
            separators=(",", ":"),
        )

    @property
    def family(self) -> str:
        """The same package coordinates across versions."""
        return json.dumps(
            [self.namespace, self.name, self.kind],
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class Record:
    """A consumer-owned payload attached to an exact reference."""

    ref: Ref
    payload: JSON | JSONSerializable = None

    def to_json(self) -> str:
        """Store the payload's JSON value, without its Python class information."""
        payload = self.payload
        if isinstance(payload, JSONSerializable):
            payload = json.loads(payload.to_json())
        return json.dumps(
            {"ref": asdict(self.ref), "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def from_json(cls, payload: str) -> Self:
        """Restore an immutable JSON snapshot; the consumer decodes its own payload."""
        value = json.loads(payload)
        return cls(Ref(**value["ref"]), deepfreeze(value["payload"]))


__all__ = ["Record", "Ref"]
