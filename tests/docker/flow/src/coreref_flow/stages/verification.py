"""Run Verilator verification through Edalize."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from shutil import copyfile
from typing import cast

from edalize.flows.lint import Lint  # type: ignore[import-untyped]
from edalize.flows.sim import Sim  # type: ignore[import-untyped]

from .rtl import read_source_list

_WARNINGS = ("-Wall", "-Wno-DECLFILENAME")


def _edam(
    work: Path, top: str, defines: Sequence[str], *, testbench: Path | None = None
) -> dict[str, object]:
    sources = read_source_list(work / "rtl")
    options = {"tool": "verilator", "verilator_options": list(_WARNINGS)}
    if testbench is not None:
        sources.append(testbench)
        options = {
            "tool": "verilator",
            "mode": "binary",
            "exe": "false",
            "verilator_options": [*_WARNINGS, "--assert", "--timescale", "1ns/1ps", "-j", "2"],
        }
    parameters = {}
    for define in defines:
        name, separator, value = define.partition("=")
        parameters[name] = {
            "paramtype": "vlogdefine",
            "datatype": "str" if separator else "bool",
            # Preserve the literal when Verilator reads the generated argument file.
            "default": json.dumps(value, ensure_ascii=False) if separator else True,
        }
    return {
        "name": "verification",
        "toplevel": top,
        "files": [
            {
                "name": str(path.relative_to(work)),
                "file_type": "systemVerilogSource" if path.suffix == ".sv" else "verilogSource",
            }
            for path in sources
        ],
        "parameters": parameters,
        "flow_options": options,
    }


def lint(work: Path, log: Path, parameters: Mapping[str, object]) -> None:
    """Lint the selected RTL with warnings treated as errors."""
    defines = cast(list[str], parameters.get("defines", []))
    backend = Lint(_edam(work, str(parameters["top"]), defines), str(work))
    with log.open("wb") as stream:
        backend.stdout = backend.stderr = stream
        backend.configure()
        backend.build()
    copyfile(log, work / "lint.log")


def _simulate(work: Path, log: Path, defines: Sequence[str]) -> None:
    testbench = (work / "testbench").rename(work / "testbench.sv")
    backend = Sim(_edam(work, "testbench", defines, testbench=testbench), str(work))
    with log.open("wb") as stream:
        backend.stdout = backend.stderr = stream
        backend.configure()
        backend.build()
        with (work / "simulation.log").open("wb") as output:
            backend.stdout = output
            backend.run()


def simulate(work: Path, log: Path, parameters: Mapping[str, object]) -> None:
    """Run the project's testbench against the supplied behavioral sources."""
    defines = cast(list[str], parameters.get("defines", []))
    extra = cast(list[str], parameters.get("extraDefines", []))
    _simulate(work, log, [*defines, *extra])


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
