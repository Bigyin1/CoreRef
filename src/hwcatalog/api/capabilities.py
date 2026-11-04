"""Storage guarantees that differ between supported backends."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class BackendCapabilities:
    atomic_publish: bool = True
    consistency: Literal["snapshot", "live"] = "snapshot"
    lockfiles: bool = False


SNAPSHOT_CAPABILITIES = BackendCapabilities()
CONAN_CAPABILITIES = BackendCapabilities(atomic_publish=False, consistency="live", lockfiles=True)
