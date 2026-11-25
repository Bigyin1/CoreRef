"""Test the Python transforms used by the Nix stage map."""

import json
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from unittest.mock import patch

import pytest

from coreref_flow.stages.memory import generate
from coreref_flow.stages.netlist_check import check_synthesis
from coreref_flow.stages.physical_lint import check_inputs
from coreref_flow.stages.rtl import assemble, assemble_dft, read_source_list
from coreref_flow.stages.verification import simulate


def test_rtl_preserves_the_declared_source_order(tmp_path: Path) -> None:
    for filename in (
        "sources/000000-library/000000/types.sv",
        "files/000000/z.sv",
        "files/000001/a.sv",
    ):
        source = tmp_path / filename
        source.parent.mkdir(parents=True)
        source.write_text(filename)

    assemble(tmp_path, tmp_path / "stage.log", {})

    bundle = tmp_path / "hdl"
    sources = read_source_list(bundle)
    assert [source.name for source in sources] == ["types.sv", "z.sv", "a.sv"]
    assert all(source.read_text() == str(source.relative_to(bundle)) for source in sources)


def test_hierarchical_dft_output_excludes_other_modules(tmp_path: Path) -> None:
    dependencies = tmp_path / "sources"
    dependencies.mkdir()
    (dependencies / "dependency").write_text("module dependency; endmodule\n")
    own = tmp_path / "files" / "000000"
    own.mkdir(parents=True)
    (own / "design.v").write_text("module design; endmodule\n")
    (tmp_path / "dft_module.v").write_text("module scan_output; endmodule\n")

    with patch("coreref_flow.stages.rtl.subprocess.run") as tcl:
        assemble_dft(tmp_path, tmp_path / "stage.log", {})

    assert tcl.call_args.args[0] == ["tclsh", "script", "dft_module.v", "files/000000/design.v"]
    assert [source.name for source in read_source_list(tmp_path / "hdl")] == [
        "dependency.v",
        "dft_module.v",
    ]
    module = (tmp_path / "dft_module.v").read_text()
    assert "module scan_output" in module
    assert "module dependency" not in module


def test_dft_propagates_a_tcl_error(tmp_path: Path) -> None:
    (tmp_path / "files").touch()
    with (
        patch("coreref_flow.stages.rtl.subprocess.run", side_effect=CalledProcessError(1, "tclsh")),
        pytest.raises(CalledProcessError),
    ):
        assemble_dft(tmp_path, tmp_path / "stage.log", {})
    assert not (tmp_path / "dft_output.v").exists()


def test_non_power_of_two_sram_depth(tmp_path: Path) -> None:
    (tmp_path / "template").write_text("sram_@DEPTH@x@WIDTH@; address_bits=@ADDR@")
    generate(tmp_path, tmp_path / "log", {"depth": 5, "width": 4})
    (source,) = read_source_list(tmp_path / "hdl")
    assert source.read_text() == "sram_5x4; address_bits=3"


def test_simulation_passes_source_list_and_defines_to_verilator(tmp_path: Path) -> None:
    (tmp_path / "testbench").write_text("module testbench; endmodule\n")
    with patch("coreref_flow.stages.verification.subprocess.run") as tool:
        tool.side_effect = [CompletedProcess([], 0), CompletedProcess([], 0, stdout=b"PASS\n")]
        simulate(tmp_path, tmp_path / "stage.log", {"defines": ["TB_STEP=2"]})

    command = tool.call_args_list[0].args[0]
    assert "-DTB_STEP=2" in command
    assert command[-3:] == ["-F", "rtl/sources.f", "testbench.sv"]
    assert (tmp_path / "simulation.log").read_bytes() == b"PASS\n"


def test_physical_lint_records_inputs_in_compilation_order(tmp_path: Path) -> None:
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "a.v").write_text("module design; endmodule")
    (rtl / "z.v").write_text("module library; endmodule")
    (rtl / "sources.f").write_text("z.v\na.v\n")
    abstract = tmp_path / "macros" / "counter" / "abstract.lef"
    abstract.parent.mkdir(parents=True)
    abstract.write_text("LEF")

    check_inputs(tmp_path, tmp_path / "stage.log", {"top": "design"})

    report = json.loads((tmp_path / "inputs.json").read_text())
    assert report["rtl"] == ["rtl/z.v", "rtl/a.v"]
    assert report["macroInputs"] == ["macros/counter/abstract.lef"]


def test_netlist_check_requires_exact_top(tmp_path: Path) -> None:
    netlist = tmp_path / "netlist"
    netlist.write_text("module unexpected_top; endmodule\n")
    with pytest.raises(ValueError, match="does not declare module top"):
        check_synthesis(tmp_path, tmp_path / "log", {"top": "top"})
    netlist.write_text("module top (a); endmodule\n")
    check_synthesis(tmp_path, tmp_path / "log", {"top": "top"})
    assert json.loads((tmp_path / "netlist-check.json").read_text()) == {"top": "top"}
