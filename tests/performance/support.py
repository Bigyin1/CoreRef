"""Small reusable package datasets; all preparation happens outside timing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Literal

from dulwich import porcelain

from coreref.api.catalog import Catalog
from coreref.api.graph import Graph
from coreref.api.records import InstalledRecord, LocalRecord, Ref
from examples.local_server import LocalServer

Shape = Literal["chain", "wide", "shared"]
Content = Literal["metadata", "files", "scm", "mixed"]


@dataclass(frozen=True)
class Dataset:
    namespace: str
    records: tuple[InstalledRecord, ...]
    roots: tuple[Ref, ...]
    edges: tuple[tuple[Ref, Ref], ...]

    def resolve(self, catalog: Catalog, *, lockfile: Path | None = None) -> Graph:
        root = LocalRecord(Ref(f"{self.namespace}.local.rtl.default/1.0", catalog=catalog))
        return Graph(
            root,
            dependencies=tuple(
                Ref(str(ref.without_revision()), catalog=catalog) for ref in self.roots
            ),
            lockfile=lockfile,
        )

    def check(self, graph: Graph) -> None:
        assert {record.ref for record in graph.records if record.ref.revision} == {
            record.ref for record in self.records
        }
        expected = {*self.edges, *((graph.record.ref, ref) for ref in self.roots)}
        assert set(graph.networkx.edges) == expected


class Registry:
    """Publish each dataset once; give every cold measurement its own reader home."""

    def __init__(self, server: LocalServer, root: Path) -> None:
        self.server = server
        self.root = root
        self.publisher = Catalog(server.configure(root / "publisher", local_sources=True))
        self.sequence = count()
        self.datasets: dict[tuple[Shape, int, Content], Dataset] = {}
        self.source = root / "source"
        self.source.mkdir()
        with porcelain.init(self.source):
            (self.source / "block.sv").write_text("module block; endmodule\n")
            porcelain.add(self.source, paths=["block.sv"])
            porcelain.commit(
                self.source,
                message=b"Source fixture",
                author=b"Perf <perf@example.invalid>",
                committer=b"Perf <perf@example.invalid>",
                sign=False,
            )
        self.artifact = root / "result.bin"
        self.artifact.write_bytes(b"artifact\n" * 512)

    def reader(self) -> Catalog:
        home = self.root / f"reader-{next(self.sequence)}"
        return Catalog(self.server.configure(home, local_sources=True))

    def dataset(self, shape: Shape, size: int, content: Content = "metadata") -> Dataset:
        key = (shape, size, content)
        if key not in self.datasets:
            namespace = f"perf-{shape}-{size}-{content}"
            records: list[InstalledRecord] = []
            edges: list[tuple[Ref, Ref]] = []
            for index in range(size):
                requirements = self._requirements(shape, records)
                record = self.publisher.publish(
                    self._record(namespace, index, content),
                    dependencies=requirements,
                )
                records.append(record)
                edges.extend((record.ref, ref) for ref in requirements)
            roots = (records[-1].ref,) if shape == "chain" else tuple(r.ref for r in records)
            self.datasets[key] = Dataset(namespace, tuple(records), roots, tuple(edges))
        return self.datasets[key]

    def _record(self, namespace: str, index: int, content: Content) -> LocalRecord:
        kind = ("files", "scm", "both")[index % 3] if content == "mixed" else content
        return LocalRecord(
            Ref(f"{namespace}.node{index:03}.rtl.default/1.0", catalog=self.publisher),
            payload={"index": index},
            source=self.source if kind in {"scm", "both"} else None,
            files={"result.bin": self.artifact} if kind in {"files", "both"} else {},
        )

    def _requirements(self, shape: Shape, records: list[InstalledRecord]) -> tuple[Ref, ...]:
        if shape == "chain" and records:
            return (records[-1].ref,)
        if shape == "shared" and len(records) >= 2:
            return (records[0].ref, records[1].ref)
        return ()


def graph_setup(
    registry: Registry,
    dataset: Dataset,
    state: str,
    *,
    sources: bool = True,
) -> Callable[[], tuple[tuple[Graph], dict[str, object]]]:
    """Prepare a graph with missing binaries, warm disk, or a populated object cache."""
    reader = registry.reader()
    graph = dataset.resolve(reader)
    if state != "cold":
        fetch(graph, sources)

    def setup() -> tuple[tuple[Graph], dict[str, object]]:
        if state == "cold":
            return (dataset.resolve(registry.reader()),), {}
        if state == "disk":
            return (dataset.resolve(reader),), {}
        return (graph,), {}

    return setup


def fetch(graph: Graph, sources: bool) -> tuple[InstalledRecord, ...]:
    return graph.get_with_sources() if sources else graph.get()


def check_files(records: tuple[InstalledRecord, ...], *, sources: bool) -> None:
    for record in records:
        assert set(record.files) == set(record.file_names)
        assert all(path.read_bytes() == b"artifact\n" * 512 for path in record.files.values())
        if sources and record.source is not None:
            assert (record.source / "block.sv").read_text() == "module block; endmodule\n"
