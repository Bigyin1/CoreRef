"""Get macro files from a selected component via an owned local Conan server."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from coreref.api.packages.catalog import Catalog
from coreref.api.packages.records import LocalRecord, Ref
from coreref.api.projects.graph import Graph
from coreref.testing import local_server


def main() -> None:
    with TemporaryDirectory(prefix="coreref-example-") as directory:
        root = Path(directory)
        with local_server(root / "server") as server:
            catalog = Catalog(server.configure(root / "cache"))
            cpu = LocalRecord(Ref("soc1.cpu/2.3"))
            early = catalog.publish(cpu)
            lib, lef = root / "cpu.lib", root / "cpu.lef"
            lib.write_text("library(cpu) {}\n")
            lef.write_text("MACRO cpu\nEND cpu\n")
            cpu.files.update({"cpu.lib": lib, "cpu.lef": lef})
            physical = catalog.publish(cpu)
            soc = LocalRecord(Ref("soc1.top/1.0"))
            project = Graph(catalog, soc, dependencies=(physical.ref,))
            (macro,) = project.get((cpu.ref,))
            print(f"Early revision: {early.ref}")
            print(f"Selected macro: {macro.ref}")
            print(macro.files["cpu.lib"].read_text().strip())


if __name__ == "__main__":
    main()
