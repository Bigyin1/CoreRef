"""Run with ``uv run python -m examples.conan_macro``; uses an owned local server."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from coreref.api.catalog import Catalog
from coreref.api.graph import Graph
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord, Ref
from examples.local_server import local_server


def main() -> None:
    with (
        TemporaryDirectory(prefix="coreref-example-") as directory,
        local_server(Path(directory) / "server") as server,
    ):
        catalog = Catalog(server.configure(Path(directory) / "cache"))
        rtl = catalog.publish(LocalRecord(Ref.from_fields("soc1", "cpu", "rtl", "default", "2.3")))
        intermediate = catalog.publish(
            LocalRecord(Ref.from_fields("tools", "dft-run", "dft", "default", "3.0")),
            dependencies=(rtl.ref,),
        )
        inputs = Path(directory) / "inputs"
        inputs.mkdir()
        lib, lef = inputs / "cpu.lib", inputs / "cpu.lef"
        lib.write_text("library(cpu) {}\n")
        lef.write_text("MACRO cpu\nEND cpu\n")
        catalog.publish(
            LocalRecord(
                Ref.from_fields("soc1", "cpu", "be", "lib-lef", "4.0"),
                {"corner": "tt"},
                files={"cpu.lib": lib, "cpu.lef": lef},
            ),
            dependencies=(intermediate.ref,),
        )
        for result in catalog.search(SearchQuery.for_component(rtl.ref, view="be", type="lib-lef")):
            print(result.ref)
            print(result.to_dict())
            graph = Graph(catalog, result.ref)
            installed = graph.get((graph.record.ref,))[0]
            print(f"{len(graph.records)} nodes, {graph.networkx.number_of_edges()} edges")
            print(installed.files["cpu.lib"].read_text().strip())


if __name__ == "__main__":
    main()
