"""Assemble behavioral RTL and hierarchical DFT inputs."""

from collections.abc import Mapping
from pathlib import Path


def _bundle(work: Path, own: bytes, output: str) -> None:
    sources = [path.read_bytes() for path in sorted((work / "sources").glob("*.v"))]
    (work / output).write_bytes(b"\n".join((*sources, own)))


def assemble(work: Path, _log: Path, _parameters: Mapping[str, object]) -> None:
    """Combine the selected dependencies and the project's RTL."""
    _bundle(work, (work / "own.v").read_bytes(), "rtl.v")


def assemble_dft(work: Path, _log: Path, parameters: Mapping[str, object]) -> None:
    """Emit the scan-enabled demonstration module and its standalone bundle."""
    own = (work / "own.v").read_bytes()
    if parameters["scan"]:
        own = b"\x60define DEMO_SCAN\n" + own + b"\n\x60undef DEMO_SCAN\n"
        (work / "dft_module.v").write_bytes(own)
    _bundle(work, own, "dft_output.v")
