"""Run the pinned Yosys and OpenROAD tools on explicit stage inputs."""

import os
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import cast

from .rtl import read_source_list, staged_sources


def _run(work: Path, log: Path, parameters: Mapping[str, object], *command: str) -> None:
    environment = {
        **os.environ,
        "COREREF_PLATFORM": str(parameters["platform"]),
        "COREREF_TOP": str(parameters["top"]),
        "COREREF_MACRO": str(parameters.get("macro", "")),
        "COREREF_MACRO_INSTANCE": str(parameters.get("macroInstance", "")),
    }
    with log.open("wb") as stream:
        subprocess.run(command, cwd=work, env=environment, stdout=stream, stderr=stream, check=True)


def _tcl_words(words: Iterable[str]) -> str:
    escaped = str.maketrans({char: "\\" + char for char in '\\"$[]'})
    return " ".join(f'"{word.translate(escaped)}"' for word in words)


def _read_rtl(work: Path, sources: list[Path], flags: list[str]) -> str:
    if not sources:
        return ""
    args = [*flags, *(str(path.relative_to(work)) for path in sources)]
    return f"yosys read_verilog -sv {_tcl_words(args)}\n"


def synthesize(work: Path, log: Path, parameters: Mapping[str, object]) -> None:
    """Map the design to Nangate45 cells, importing macro ports as a black box."""
    defines = [f"-D{define}" for define in cast(list[str], parameters.get("defines", []))]
    blackbox = _read_rtl(work, staged_sources(work / "macro-rtl"), ["-lib", *defines])
    design = _read_rtl(work, read_source_list(work / "rtl"), defines)
    (work / "synth.tcl").write_text(
        "set lib $::env(COREREF_PLATFORM)/lib/NangateOpenCellLibrary_typical.lib\n"
        "yosys read_liberty -lib $lib\n"
        f"{blackbox}"
        f"{design}"
        "yosys synth -flatten -top $::env(COREREF_TOP) -noabc\n"
        "yosys dfflibmap -liberty $lib\n"
        "yosys abc -liberty $lib\n"
        "yosys hilomap -singleton -hicell LOGIC1_X1 Z -locell LOGIC0_X1 Z\n"
        "yosys check -assert\n"
        "yosys write_verilog -noattr -noexpr netlist.v\n",
        encoding="utf-8",
    )
    _run(work, log, parameters, "yosys", "-c", "synth.tcl")


def openroad(work: Path, log: Path, parameters: Mapping[str, object]) -> None:
    """Run the Tcl script selected for this stage."""
    _run(work, log, parameters, "openroad", "-threads", "2", "-exit", str(parameters["script"]))
