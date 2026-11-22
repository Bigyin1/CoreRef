"""Catalog fixture that records successful publications for failure reports."""

from pathlib import Path

from ..api.packages.catalog import Catalog
from ..api.packages.records import Dependency, InstalledRecord, LocalRecord, Ref
from ..api.projects.graph import Graph


class _RecordingCatalog(Catalog):
    def __init__(self, home: Path, published: list[Ref], *, timeout: float) -> None:
        super().__init__(home, timeout=timeout)
        self._published = published

    def publish(
        self,
        record: LocalRecord | Graph,
        *,
        dependencies: tuple[Dependency, ...] = (),
        lockfile: Path | None = None,
    ) -> InstalledRecord:
        installed = super().publish(record, dependencies=dependencies, lockfile=lockfile)
        self._published.append(installed.ref)
        return installed

    def _publish_graph(self, graph: Graph, record: LocalRecord) -> InstalledRecord:
        installed = super()._publish_graph(graph, record)
        self._published.append(installed.ref)
        return installed
