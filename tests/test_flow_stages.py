"""Test the Python transforms used by the Nix stage map."""

import json
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from unittest.mock import patch

import pytest

from coreref_flow.stages.memory import generate
from coreref_flow.stages.netlist_check import check_synthesis
from coreref_flow.stages.rtl import assemble_dft
from coreref_flow.stages.verification import simulate


def test_hierarchical_dft_output_excludes_other_modules(tmp_path: Path) -> None:
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "dependency.v").write_text("module dependency; endmodule\n")
    (tmp_path / "dft_module.v").write_text("module scan_output; endmodule\n")
    with patch("coreref_flow.stages.rtl.subprocess.run") as tcl:
        assemble_dft(tmp_path, tmp_path / "stage.log", {})
    assert tcl.call_args.args[0] == ["tclsh", "script", "rtl", "dft_module.v"]
    module = (tmp_path / "dft_module.v").read_text()
    assert "module scan_output" in module
    assert "module dependency" not in module
    assert "module dependency" in (tmp_path / "dft_output.v").read_text()


def test_dft_propagates_a_tcl_error(tmp_path: Path) -> None:
    with (
        patch("coreref_flow.stages.rtl.subprocess.run", side_effect=CalledProcessError(1, "tclsh")),
        pytest.raises(CalledProcessError),
    ):
        assemble_dft(tmp_path, tmp_path / "stage.log", {})
    assert not (tmp_path / "dft_output.v").exists()


def test_non_power_of_two_sram_depth(tmp_path: Path) -> None:
    (tmp_path / "template").write_text("sram_@DEPTH@x@WIDTH@; address_bits=@ADDR@")
    generate(tmp_path, tmp_path / "log", {"depth": 5, "width": 4})
    assert (tmp_path / "rtl.v").read_text() == "sram_5x4; address_bits=3"


def test_simulation_prepares_files_for_verilator(tmp_path: Path) -> None:
    (tmp_path / "rtl").write_text("module design; endmodule\n")
    (tmp_path / "testbench").write_text("module testbench; endmodule\n")
    with patch("coreref_flow.stages.verification.subprocess.run") as tool:
        tool.side_effect = [CompletedProcess([], 0), CompletedProcess([], 0, stdout=b"PASS\n")]
        simulate(tmp_path, tmp_path / "stage.log", {})
    assert tool.call_args_list[0].args[0][-2:] == ["rtl.v", "testbench.sv"]
    assert (tmp_path / "rtl.v").read_text() == "module design; endmodule\n"
    assert (tmp_path / "testbench.sv").read_text() == "module testbench; endmodule\n"
    assert (tmp_path / "simulation.log").read_bytes() == b"PASS\n"


def test_netlist_check_requires_exact_top(tmp_path: Path) -> None:
    netlist = tmp_path / "netlist"
    netlist.write_text("module unexpected_top; endmodule\n")
    with pytest.raises(ValueError, match="does not declare module top"):
        check_synthesis(tmp_path, tmp_path / "log", {"top": "top"})
    netlist.write_text("module top (a); endmodule\n")
    check_synthesis(tmp_path, tmp_path / "log", {"top": "top"})
    assert json.loads((tmp_path / "netlist-check.json").read_text()) == {"top": "top"}
