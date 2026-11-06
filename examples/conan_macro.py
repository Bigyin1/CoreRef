"""Run with ``uv run python -m examples.conan_macro``; uses an owned local server."""

from pathlib import Path
from tempfile import TemporaryDirectory

from examples.local_server import local_server
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.graph import Graph
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import LocalRecord, Ref


def main() -> None:
    with (
        TemporaryDirectory(prefix="hwcatalog-example-") as directory,
        local_server(Path(directory) / "server") as server,
    ):
        catalog = Catalog(server.configure(Path(directory) / "cache"))
        rtl = catalog.publish(
            LocalRecord(Ref("soc1", "cpu", "rtl", "default", "2.3", catalog=catalog))
        )
        intermediate = catalog.publish(
            LocalRecord(Ref("tools", "dft-run", "dft", "default", "3.0", catalog=catalog)),
            dependencies=(rtl.ref,),
        )
        inputs = Path(directory) / "inputs"
        inputs.mkdir()
        lib, lef = inputs / "cpu.lib", inputs / "cpu.lef"
        lib.write_text("library(cpu) {}\n")
        lef.write_text("MACRO cpu\nEND cpu\n")
        catalog.publish(
            LocalRecord(
                Ref("soc1", "cpu", "be", "lib-lef", "4.0", catalog=catalog),
                {"corner": "tt"},
                files={"cpu.lib": lib, "cpu.lef": lef},
            ),
            dependencies=(intermediate.ref,),
        )
        for result in rtl.ref.find_dependents(
            candidates=SearchQuery(namespace="soc1", component="cpu", view="be", type="lib-lef")
        ):
            print(result.ref)
            print(result.to_dict())
            installed = result.ref.get()
            dependencies = tuple(record.ref for record in result.ref.dependencies())
            graph = Graph(
                catalog,
                LocalRecord(result.ref.without_revision(), result.payload, files=installed.files),
                dependencies=dependencies,
            )
            print(f"{len(graph.records)} nodes, {len(graph.dependencies)} edges")
            print(installed.files["cpu.lib"].read_text().strip())


if __name__ == "__main__":
    main()
