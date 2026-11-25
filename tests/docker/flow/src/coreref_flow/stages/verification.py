"""Compile and run the selected RTL or hierarchical DFT testbench."""

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

_WARNINGS = ("-Wall", "-Wno-DECLFILENAME")


def lint(work: Path, log: Path, parameters: Mapping[str, object]) -> None:
    """Run Verilator with warnings treated as errors."""
    command = ["verilator", *_WARNINGS, "--lint-only", "--top-module", str(parameters["top"])]
    rtl = (work / "rtl").rename(work / "rtl.v")
    with log.open("wb") as stream:
        subprocess.run([*command, rtl.name], cwd=work, stdout=stream, stderr=stream, check=True)
    (work / "lint.log").write_bytes(log.read_bytes())


def _simulate(work: Path, log: Path, defines: Sequence[str]) -> None:
    rtl = (work / "rtl").rename(work / "rtl.v")
    testbench = (work / "testbench").rename(work / "testbench.sv")
    command = [
        "verilator",
        *_WARNINGS,
        *(f"-D{define}" for define in defines),
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
        rtl.name,
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
    _simulate(work, log, cast(list[str], parameters.get("defines", [])))


def simulate_sram(work: Path, log: Path, parameters: Mapping[str, object]) -> None:
    """Specialize the shared SRAM testbench for the generated RTL."""
    _simulate(
        work,
        log,
        [
            f"TB_SRAM={parameters['top']}",
            f"TB_DEPTH={parameters['depth']}",
            f"TB_WIDTH={parameters['width']}",
        ],
    )
