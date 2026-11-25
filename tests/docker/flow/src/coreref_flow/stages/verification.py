"""Compile and run the selected RTL or hierarchical DFT testbench."""

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

_WARNINGS = ("-Wall", "-Wno-DECLFILENAME")


def lint(work: Path, log: Path, parameters: Mapping[str, object]) -> None:
    """Run Verilator with warnings treated as errors."""
    command = ["verilator", *_WARNINGS, "--lint-only", "--top-module", str(parameters["top"])]
    with log.open("wb") as stream:
        subprocess.run(
            [*command, *_defines(parameters), "-F", "rtl/sources.f"],
            cwd=work,
            stdout=stream,
            stderr=stream,
            check=True,
        )
    (work / "lint.log").write_bytes(log.read_bytes())


def _defines(parameters: Mapping[str, object]) -> list[str]:
    return [f"-D{define}" for define in cast(list[str], parameters.get("defines", []))]


def _simulate(work: Path, log: Path, flags: Sequence[str]) -> None:
    testbench = (work / "testbench").rename(work / "testbench.sv")
    command = [
        "verilator",
        *_WARNINGS,
        *flags,
        "--binary",
        "--assert",
        "--timescale",
        "1ns/1ps",
        "-j",
        "2",
        "--top-module",
        "testbench",
        "-o",
        "simulate",
        "-F",
        "rtl/sources.f",
        testbench.name,
    ]
    with log.open("wb") as stream:
        subprocess.run(command, cwd=work, stdout=stream, stderr=stream, check=True)
        result = subprocess.run(
            [str(work / "obj_dir" / "simulate")],
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=stream,
            check=True,
        )
    (work / "simulation.log").write_bytes(result.stdout)


def simulate(work: Path, log: Path, parameters: Mapping[str, object]) -> None:
    """Run the project's testbench against the supplied behavioral sources."""
    extra = cast(list[str], parameters.get("extraDefines", []))
    _simulate(work, log, [*_defines(parameters), *(f"-D{define}" for define in extra)])


def simulate_sram(work: Path, log: Path, parameters: Mapping[str, object]) -> None:
    """Specialize the shared SRAM testbench for the generated RTL."""
    _simulate(
        work,
        log,
        [
            f"-DTB_SRAM={parameters['top']}",
            f"-DTB_DEPTH={parameters['depth']}",
            f"-DTB_WIDTH={parameters['width']}",
        ],
    )
