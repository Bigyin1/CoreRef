"""Run the pinned Yosys and OpenROAD tools on explicit stage inputs."""

import os
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import cast

from .rtl import read_defines, read_source_list, staged_sources, write_source_list


def _run(work: Path, log: Path, parameters: Mapping[str, object], *command: str) -> None:
    environment = {
        **os.environ,
        "COREREF_PLATFORM": str(parameters["platform"]),
        "COREREF_TOP": str(parameters["top"]),
    }
    if "macroCells" in parameters:
        cells = cast(list[str], parameters["macroCells"])
        placements = cast(dict[str, list[int]], parameters.get("placements", {}))
        environment["COREREF_MACRO_CELLS"] = _tcl_words(cells)
        environment["COREREF_MACRO_PLACEMENTS"] = _tcl_words(
            _tcl_words((instance, *map(str, location))) for instance, location in placements.items()
        )
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


def _design(work: Path, macro_flags: list[str]) -> str:
    """Read the selected RTL bundle and nested macro inputs in order."""
    rtl = work / "rtl"
    defines = [f"-D{define}" for define in read_defines(rtl)]
    macros = "".join(
        _read_rtl(work, staged_sources(macro / "rtl"), macro_flags)
        for macro in sorted((work / "macros").glob("*"))
    )
    return macros + _read_rtl(work, read_source_list(rtl), defines)


def model(work: Path, log: Path, parameters: Mapping[str, object]) -> None:
    """Elaborate one functional model with the producer's synthesis frontend."""
    bundle = work / "hdl"
    bundle.mkdir()
    (work / "model.tcl").write_text(
        _design(work, []) + "yosys hierarchy -check -top $::env(COREREF_TOP)\n"
        "yosys proc\nyosys flatten -noscopeinfo\nyosys opt\nyosys splitnets\nyosys clean -purge\n"
        "yosys write_verilog -noattr hdl/model.v\n",
        encoding="utf-8",
    )
    _run(work, log, parameters, "yosys", "-c", "model.tcl")
    write_source_list(bundle, [bundle / "model.v"])


def synthesize(work: Path, log: Path, parameters: Mapping[str, object]) -> None:
    """Map the design to Nangate45 cells, importing macro ports as a black box."""
    design = _design(work, ["-lib"])
    (work / "synth.tcl").write_text(
        "set lib $::env(COREREF_PLATFORM)/lib/NangateOpenCellLibrary_typical.lib\n"
        "yosys read_liberty -lib $lib\n"
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
