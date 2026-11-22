"""Hash declared build inputs and share verified stage outputs through CoreRef."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from shutil import copyfile

from .._internal.artifacts import ArtifactStore, OutputIndex
from .artifacts import file_digest, fingerprint
from .catalog import Catalog
from .query import SearchQuery
from .records import JSON, LocalRecord, Ref


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

    def _key(self, directory: Path, inputs: tuple[str, ...]) -> str:
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
        self._catalog = catalog
        self.mode = mode
        self._artifacts = ArtifactStore(catalog)

    def run(
        self,
        invocation: StageInvocation,
        directory: Path,
        *,
        inputs: tuple[str, ...],
        outputs: tuple[str, ...],
        action: Callable[[], None],
    ) -> str | None:
        """Run the action on a cache miss. Return the key only when restored."""
        if self.mode is CacheMode.OFF:
            action()
            return None
        key = invocation._key(directory, inputs)
        if self._restore(invocation.name, key, directory, outputs):
            return key
        action()
        if self.mode is CacheMode.WRITE:
            self._publish(invocation.name, key, directory, outputs)
        return None

    def _restore(self, stage: str, key: str, directory: Path, outputs: tuple[str, ...]) -> bool:
        refs = self._catalog.search_refs(
            SearchQuery(namespace="coreref-stage", component=stage, version=key),
            all_revisions=True,
        )
        if not refs:
            return False
        if len(refs) != 1:
            raise ValueError(f"Conflicting shared cache entries for {stage}/{key}")
        index = OutputIndex.from_record(self._catalog.info(refs[0]))
        if index is None or index.key != key or set(index.outputs) != set(outputs):
            raise ValueError(f"Invalid shared cache index {refs[0]}")
        for name, artifact in index.outputs.items():
            output = directory / name
            output.parent.mkdir(parents=True, exist_ok=True)
            copyfile(self._artifacts.get(artifact), output)
        return True

    def _publish(self, stage: str, key: str, directory: Path, outputs: tuple[str, ...]) -> None:
        index = self._artifacts.pack_index(key, {name: directory / name for name in outputs})
        self._artifacts.upload(index.outputs)
        self._catalog.publish(
            LocalRecord(Ref(f"coreref-stage.{stage}/{key}"), {"output_index": index.document()})
        )
