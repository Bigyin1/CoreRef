"""Run with ``uv run python examples/conan_macro.py``; uses a disposable local cache."""

import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.backends.conan import ConanBackend


def main() -> None:
    rtl = Record(Ref("cpu", "2.3", "rtl", "soc1"), {"files": ["/project/cpu.sv"]})
    intermediate = Record(Ref("dft-run", "3.0", "result", "tools"))
    macro = Record(Ref("cpu", "4.0", "lib-lef", "soc1"))
    with (
        TemporaryDirectory(prefix="hwcatalog-example-") as directory,
        Catalog(ConanBackend(Path(directory))) as catalog,
    ):
        catalog.publish(rtl)
        catalog.publish(intermediate, dependencies=(rtl.ref,))
        inputs = Path(directory) / "inputs"
        inputs.mkdir()
        (inputs / "cpu.lib").write_text("library(cpu) {}\n")
        (inputs / "cpu.lef").write_text("MACRO cpu\nEND cpu\n")
        manifest = inputs / "package.json"
        manifest.write_text(
            json.dumps(
                {
                    "ref": asdict(macro.ref),
                    "payload": {"corner": "tt"},
                    "dependencies": [asdict(intermediate.ref)],
                    "files": {"cpu.lib": "cpu.lib", "cpu.lef": "cpu.lef"},
                }
            )
        )
        catalog.publish_file(manifest)
        candidates = SearchQuery(namespace="soc1", name="cpu", kind="lib-lef")
        for result in catalog.find_dependents(rtl.ref, candidates=candidates):
            print(result.ref.identity)
            print(result.to_json())
            graph = catalog.graph.dependency_closure(result.ref)
            print(f"{len(graph.records)} nodes, {len(graph.dependencies)} edges")
            content = catalog.materialize(result.ref).path
            print((content / "artifacts/cpu.lib").read_text().strip())


if __name__ == "__main__":
    main()
