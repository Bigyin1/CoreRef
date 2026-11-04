"""Graph relation and traversal direction vocabularies."""

from enum import StrEnum


class RelationKind(StrEnum):
    REQUIRES = "REQUIRES"
    IMPLEMENTS = "IMPLEMENTS"
    USED = "USED"
    PRODUCED = "PRODUCED"
    BACKWARD_COMPATIBLE_WITH = "BACKWARD_COMPATIBLE_WITH"


class Direction(StrEnum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"


class LineageDirection(StrEnum):
    UPSTREAM = "upstream"
    DOWNSTREAM = "downstream"


__all__ = ["Direction", "LineageDirection", "RelationKind"]
