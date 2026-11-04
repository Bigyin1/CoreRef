"""Storage guarantees available to callers without probing operations."""

from dataclasses import dataclass
from typing import Literal

from .enums import RelationKind


@dataclass(frozen=True)
class BackendCapabilities:
    relation_kinds: frozenset[RelationKind] = frozenset(RelationKind)
    relation_attributes: bool = True
    version_resolution: bool = True
    atomic_publish: bool = True
    consistency: Literal["snapshot", "live"] = "snapshot"
    lockfiles: bool = False


FULL_CAPABILITIES = BackendCapabilities()
CONAN_CAPABILITIES = BackendCapabilities(
    relation_kinds=frozenset({RelationKind.REQUIRES}),
    relation_attributes=False,
    version_resolution=False,
    atomic_publish=False,
    consistency="live",
    lockfiles=True,
)
