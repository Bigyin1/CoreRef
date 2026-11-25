"""Run the pinned Yosys and OpenROAD tools on explicit stage inputs."""

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path


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


def synthesize(work: Path, log: Path, parameters: Mapping[str, object]) -> None:
    """Map the design to Nangate45 cells, importing macro ports as a black box."""
    blackbox = "yosys read_verilog -lib macro_rtl.v\n" if (work / "macro_rtl.v").is_file() else ""
    (work / "synth.tcl").write_text(
        "set lib $::env(COREREF_PLATFORM)/lib/NangateOpenCellLibrary_typical.lib\n"
        "yosys read_liberty -lib $lib\n"
        f"{blackbox}"
        "yosys read_verilog design.v\n"
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
