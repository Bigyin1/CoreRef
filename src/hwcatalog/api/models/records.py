"""Exact references and records with consumer-owned JSON payloads."""

import json
from dataclasses import asdict, dataclass
from typing import Self

from frozendict import deepfreeze

from ..protocols import JSONSerializable
from ..types import JSON
from .resources import GitResource


@dataclass(frozen=True)
class Ref:
    """Exact name/version and consumer-defined kind within a namespace."""

    name: str
    version: str
    kind: str
    namespace: str

    @property
    def package_name(self) -> str:
        """The namespace, component and representation without a version."""
        return f"{self.namespace}.{self.name}.{self.kind}"

    def __str__(self) -> str:
        return f"{self.package_name}/{self.version}"

    def __lt__(self, other: Ref) -> bool:
        if not isinstance(other, Ref):
            return NotImplemented
        return (self.namespace, self.name, self.version, self.kind) < (
            other.namespace,
            other.name,
            other.version,
            other.kind,
        )


@dataclass(frozen=True)
class Record:
    """A consumer-owned payload attached to an exact reference."""

    ref: Ref
    payload: JSON | JSONSerializable = None
    source: GitResource | None = None
    files: tuple[str, ...] = ()

    def to_json(self) -> str:
        """Store the payload's JSON value, without its Python class information."""
        payload = self.payload
        if isinstance(payload, JSONSerializable):
            payload = json.loads(payload.to_json())
        value = {"ref": asdict(self.ref), "payload": payload}
        if self.source is not None:
            value["source"] = asdict(self.source)
        if self.files:
            value["files"] = self.files
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def from_json(cls, payload: str) -> Self:
        """Restore an immutable JSON snapshot; the consumer decodes its own payload."""
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("Record must be a JSON object")
        source = GitResource(**value["source"]) if value.get("source") is not None else None
        return cls(
            Ref(**value["ref"]), deepfreeze(value["payload"]), source, tuple(value.get("files", ()))
        )


__all__ = ["Record", "Ref"]
