"""Inputs that identify a project slice and its contents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..api.records import JSON, Ref


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(document: JSON) -> str:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True)
class SliceInputs:
    """Inputs that identify a project slice independently of the requested stage."""

    project: Ref
    commit: str | None
    lockfile: Path
    dependencies: tuple[Ref, ...]
    environment: JSON
    variants: Mapping[str, str] = field(default_factory=dict)
    local_outputs: Mapping[str, Path] = field(default_factory=dict)
    input_files: Mapping[str, Path | None] | None = None

    def key(self) -> str:
        """Hash the resolved inputs, including output bytes for an unpublished worktree."""
        local_outputs = self.local_outputs if self.commit is None else {}
        inputs: dict[str, JSON] = {
            "project": str(self.project),
            "local_outputs": {name: file_digest(path) for name, path in local_outputs.items()},
            "lockfile": file_digest(self.lockfile),
            "dependencies": tuple(sorted(map(str, self.dependencies))),
            "variants": self.variants,
            "environment": self.environment,
        }
        if self.input_files is None:
            return fingerprint({"schema_version": 1, "commit": self.commit, **inputs})
        files = {
            name: file_digest(path) if path is not None else None
            for name, path in self.input_files.items()
        }
        return fingerprint(
            {"schema_version": 2, "worktree": self.commit is None, "files": files, **inputs}
        )
