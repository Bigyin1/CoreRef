"""Run with ``uv run python examples/conan_macro.py``; uses a disposable local cache."""

from pathlib import Path
from tempfile import TemporaryDirectory

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.models.graph import Dependency
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.backends.conan import ConanBackend, to_conan_reference


def main() -> None:
    rtl = Record(Ref("cpu", "2.3", "rtl", "soc1"), {"files": ["/project/cpu.sv"]})
    intermediate = Record(Ref("dft-run", "3.0", "result", "tools"))
    macro = Record(
        Ref("cpu", "4.0", "lib-lef", "soc1"),
        {"files": ["/artifacts/cpu.lib", "/artifacts/cpu.lef"]},
    )
    with (
        TemporaryDirectory(prefix="hwcatalog-example-") as directory,
        Catalog(backend := ConanBackend(Path(directory))) as catalog,
    ):
        catalog.publish(
            (rtl, intermediate, macro),
            dependencies=(
                Dependency(macro.ref, intermediate.ref),
                Dependency(intermediate.ref, rtl.ref),
            ),
        )
        candidates = SearchQuery(namespace="soc1", name="cpu", kind="lib-lef")
        for result in catalog.find_dependents(rtl.ref, candidates=candidates):
            print(to_conan_reference(result.ref))
            print(result.to_json())
            graph = catalog.graph.dependency_closure(result.ref)
            print(
                f"{len(graph.records)} nodes, {len(graph.dependencies)} edges, {graph.consistency}"
            )
            native = backend.load_graph(result.ref)
            for package in native.root.conanfile.dependencies.host.topological_sort.values():
                print(f"native dependency: {package.ref}")


if __name__ == "__main__":
    main()
