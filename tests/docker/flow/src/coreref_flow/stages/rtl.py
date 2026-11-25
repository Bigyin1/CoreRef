"""Assemble behavioral RTL and hierarchical DFT inputs."""

import subprocess
from collections.abc import Mapping
from pathlib import Path


def _bundle(work: Path, own: bytes, output: str) -> None:
    sources = [path.read_bytes() for path in sorted((work / "sources").glob("*.v"))]
    (work / output).write_bytes(b"\n".join((*sources, own)))


def assemble(work: Path, _log: Path, _parameters: Mapping[str, object]) -> None:
    """Combine the selected dependencies and the project's RTL."""
    _bundle(work, (work / "own.v").read_bytes(), "rtl.v")


def assemble_dft(work: Path, log: Path, _parameters: Mapping[str, object]) -> None:
    """Run the project script on its own RTL and bundle the selected dependencies."""
    module = work / "dft_module.v"
    with log.open("wb") as stream:
        subprocess.run(
            ["tclsh", "dft.tcl", "own.v", module.name],
            cwd=work,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=True,
        )
    _bundle(work, module.read_bytes(), "dft_output.v")
