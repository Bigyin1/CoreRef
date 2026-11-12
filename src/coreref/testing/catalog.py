"""Catalog fixture that records successful publications for failure reports."""

from pathlib import Path

from ..api.catalog import Catalog
from ..api.graph import Graph
from ..api.records import InstalledRecord, LocalRecord, Ref


class _RecordingCatalog(Catalog):
    def __init__(self, home: Path, published: list[Ref], *, timeout: float) -> None:
        super().__init__(home, timeout=timeout)
        self._published = published

    def publish(
        self,
        record: LocalRecord | Graph,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
    ) -> InstalledRecord:
        installed = super().publish(record, dependencies=dependencies, lockfile=lockfile)
        self._published.append(installed.ref)
        return installed
