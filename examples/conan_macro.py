"""Run with ``uv run python examples/conan_macro.py``; uses a disposable local cache."""

from pathlib import Path
from tempfile import TemporaryDirectory

from conan_config import configured_home

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.graph import Graph
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import Record, Ref


def main() -> None:
    with TemporaryDirectory(prefix="hwcatalog-example-") as directory:
        catalog = Catalog(configured_home(Path(directory)))
        rtl = catalog.publish(Record(Ref("soc1", "cpu", "rtl", "default", "2.3")))
        intermediate = catalog.publish(
            Record(Ref("tools", "dft-run", "dft", "default", "3.0")), dependencies=(rtl.ref,)
        )
        inputs = Path(directory) / "inputs"
        inputs.mkdir()
        lib, lef = inputs / "cpu.lib", inputs / "cpu.lef"
        lib.write_text("library(cpu) {}\n")
        lef.write_text("MACRO cpu\nEND cpu\n")
        catalog.publish(
            Record(Ref("soc1", "cpu", "be", "lib-lef", "4.0"), {"corner": "tt"}),
            dependencies=(intermediate.ref,),
            files={"cpu.lib": lib, "cpu.lef": lef},
        )
        candidates = SearchQuery(namespace="soc1", component="cpu", view="be", type="lib-lef")
        for result in rtl.ref.find_dependents(candidates=candidates):
            print(result.ref)
            print(result.to_json())
            dependencies = tuple(record.ref for record in result.ref.dependencies())
            graph = Graph(catalog, result, dependencies=dependencies).resolve()
            print(f"{len(graph.records)} nodes, {len(graph.dependencies)} edges")
            content = result.ref.materialize().path
            print((content / "artifacts/cpu.lib").read_text().strip())


if __name__ == "__main__":
    main()
