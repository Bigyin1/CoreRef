"""Hash declared build inputs and share verified stage outputs through CoreRef."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from shutil import copyfile

from ..api.catalog import Catalog
from ..api.query import SearchQuery
from ..api.records import JSON, LocalRecord, Ref
from .artifacts import ArtifactStore, OutputIndex, file_digest, fingerprint


class CacheMode(str, Enum):
    OFF = "off"
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True)
class StageInvocation:
    """Tool identity and arguments for one isolated build stage."""

    name: str
    command: tuple[str, ...]
    image: str
    platform: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", self.image):
            raise ValueError("Stage caching requires an OCI image pinned by SHA-256")

    @property
    def configuration(self) -> dict[str, JSON]:
        return {
            "schema_version": 1,
            "stage": self.name,
            "image": self.image.rsplit("@", 1)[1],
            "platform": self.platform,
            "command": self.command,
        }

    def key(self, directory: Path, inputs: tuple[str, ...]) -> str:
        """Hash the named bytes staged for execution, independent of checkout path."""
        return fingerprint(
            {
                **self.configuration,
                "inputs": {name: file_digest(directory / name) for name in inputs},
            }
        )


class StageCache:
    """Restore or upload verified outputs for an exact build invocation."""

    def __init__(self, catalog: Catalog, mode: CacheMode) -> None:
        self.catalog = catalog
        self.mode = mode
        self.artifacts = ArtifactStore(catalog)

    def restore(self, stage: str, key: str, directory: Path, *, outputs: tuple[str, ...]) -> bool:
        """Return False for a missing entry and reject inconsistent or damaged entries."""
        if self.mode is CacheMode.OFF:
            return False
        refs = self.catalog.search_refs(
            SearchQuery(namespace="coreref-stage", component=stage, version=key),
            all_revisions=True,
        )
        if not refs:
            return False
        if len(refs) != 1:
            raise ValueError(f"Conflicting shared cache entries for {stage}/{key}")
        index = OutputIndex.from_record(self.catalog.info(refs[0]))
        if index is None or index.key != key or set(index.outputs) != set(outputs):
            raise ValueError(f"Invalid shared cache index {refs[0]}")
        for name, artifact in index.outputs.items():
            output = directory / name
            output.parent.mkdir(parents=True, exist_ok=True)
            copyfile(self.artifacts.get(artifact), output)
        return True

    def publish(self, stage: str, key: str, directory: Path, *, outputs: tuple[str, ...]) -> None:
        """Publish verified files before exposing their output index in write mode."""
        if self.mode is not CacheMode.WRITE:
            return
        index = self.artifacts.pack_index(key, {name: directory / name for name in outputs})
        self.artifacts.upload(index.outputs)
        self.catalog.publish(
            LocalRecord(Ref(f"coreref-stage.{stage}/{key}"), {"output_index": index.document()})
        )
