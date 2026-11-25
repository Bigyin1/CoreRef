"""Build ordered Verilog source lists for the selected route."""

import shutil
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import cast


def read_source_list(directory: Path) -> list[Path]:
    """Read paths in compilation order, relative to the list's directory."""
    return [
        directory / entry
        for entry in (directory / "sources.f").read_text(encoding="utf-8").splitlines()
    ]


def read_defines(directory: Path) -> list[str]:
    """Read the explicit compile options of this RTL bundle."""
    defines = directory / "defines.f"
    return defines.read_text(encoding="utf-8").splitlines() if defines.is_file() else []


def write_source_list(directory: Path, sources: Iterable[Path]) -> None:
    """Write a tool-readable list next to its source files."""
    entries = [str(path.relative_to(directory)) for path in sources]
    (directory / "sources.f").write_text("\n".join(entries) + "\n", encoding="utf-8")


def staged_sources(path: Path) -> list[Path]:
    """Get sources from an existing list, an ordered Nix input, or a single file."""
    if path.is_file():
        return [path]
    if (path / "sources.f").is_file():
        return read_source_list(path)
    return sorted(path.glob("*/*"))


def _dependencies(work: Path) -> list[Path]:
    return [
        source
        for component in sorted((work / "sources").glob("*"))
        for source in staged_sources(component)
    ]


def _assemble(work: Path, sources: list[Path], parameters: Mapping[str, object]) -> None:
    directory = work / "hdl"
    staged = []
    for source in sources:
        target = directory / source.relative_to(work)
        if not target.suffix:
            target = target.with_suffix(".v")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        staged.append(target)
    write_source_list(directory, staged)
    defines = cast(list[str], parameters.get("defines", []))
    if defines:
        (directory / "defines.f").write_text("\n".join(defines) + "\n", encoding="utf-8")


def _own_sources(work: Path) -> list[Path]:
    own = staged_sources(work / "files")
    if not own:
        raise ValueError("The project declares no RTL sources")
    return own


def assemble(work: Path, _log: Path, parameters: Mapping[str, object]) -> None:
    """Prepare dependency sources before the project's declared RTL."""
    _assemble(work, [*_dependencies(work), *_own_sources(work)], parameters)


def assemble_dft(work: Path, log: Path, parameters: Mapping[str, object]) -> None:
    """Replace the project's RTL with its DFT output and retain dependency files."""
    module = work / "dft_module.v"
    sources = [str(source.relative_to(work)) for source in _own_sources(work)]
    with log.open("wb") as stream:
        subprocess.run(
            ["tclsh", "script", module.name, *sources],
            cwd=work,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=True,
        )
    _assemble(work, [*_dependencies(work), module], parameters)
