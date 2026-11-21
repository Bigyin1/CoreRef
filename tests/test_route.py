from pathlib import Path

import pytest

from coreref.tools.route import Route, RouteError


def test_target_runs_shared_predecessor_once(tmp_path: Path) -> None:
    pytest.importorskip("doit")
    calls: list[str] = []
    netlist = tmp_path / "netlist"

    def synthesize() -> None:
        calls.append("synth")
        netlist.write_text("synthesized")

    route = Route()
    route.stage("rtl", action=lambda: calls.append("rtl"))
    route.stage("synth", after=("rtl",), action=synthesize, outputs={"netlist": netlist})
    route.stage("check", after=("rtl",), action=lambda: calls.append("check"))
    route.stage("dft", after=("synth", "check"), action=lambda: calls.append("dft"))
    route.stage("other", action=lambda: calls.append("other"))

    assert route.run("dft") == {"netlist": netlist}
    assert calls[0] == "rtl"
    assert set(calls[1:-1]) == {"synth", "check"}
    assert calls[-1] == "dft"


def test_missing_stage_dependency_fails_before_running() -> None:
    route = Route()
    route.stage("dft", after=("rtl",), action=lambda: pytest.fail("route ran before validation"))
    with pytest.raises(RouteError, match="unknown stages"):
        route.run()


def test_missing_output_fails(tmp_path: Path) -> None:
    pytest.importorskip("doit")
    route = Route()
    route.stage("synth", action=lambda: None, outputs={"netlist": tmp_path / "missing"})
    with pytest.raises(RouteError, match="Route failed"):
        route.run()


def test_failed_stage_stops_dependent_actions() -> None:
    def fail() -> None:
        raise RuntimeError("tool failed")

    route = Route()
    route.stage("rtl", action=fail)
    route.stage("synth", after=("rtl",), action=lambda: pytest.fail("ran after failed RTL"))
    with pytest.raises(RouteError, match="Route failed"):
        route.run("synth")
