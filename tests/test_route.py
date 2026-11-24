"""Check the resolved graph of a flow's declared stages."""

import pytest

from coreref.api.build.route import Route, StageSpec
from coreref.api.packages import Catalog


@pytest.mark.nix
def test_nix_plan_visits_shared_stage_once() -> None:
    route = Route(
        [
            StageSpec(name="rtl", outputs=("rtl.v", "report.txt")),
            StageSpec(name="dft", needs=("rtl",), outputs=("dft_output.v",)),
            StageSpec(name="synth", needs=("rtl", "dft"), outputs=("netlist.v",)),
            StageSpec(name="unrelated", outputs=("other.v",)),
        ]
    )
    plan = Catalog()._nix.plan(route, ("synth",))
    assert tuple(plan) == ("rtl", "dft", "synth")
    assert plan["synth"] == {
        "rtl.v": {"stage": "rtl", "output": "out"},
        "report.txt": {"stage": "rtl", "output": "file1"},
        "dft_output.v": {"stage": "dft", "output": "out"},
    }


@pytest.mark.nix
def test_publication_includes_transitive_checks() -> None:
    route = Route(
        [
            StageSpec(name="synth", outputs=("netlist.v",), checks=("compare",)),
            StageSpec(name="compare", outputs=(), needs=("synth",), checks=("report",)),
            StageSpec(name="report", outputs=("report.txt",), needs=("compare",)),
            StageSpec(name="lint", outputs=(), purpose="development"),
        ]
    )
    nix = Catalog()._nix
    assert tuple(nix.plan(route, ("synth",))) == ("synth",)
    assert tuple(nix.plan(route, ("synth",), publishing=True)) == (
        "synth",
        "compare",
        "report",
    )
    with pytest.raises(ValueError, match="Expected a release stage"):
        nix.plan(route, ("lint",), publishing=True)


@pytest.mark.parametrize(
    ("stages", "message"),
    (
        ([StageSpec(name="source", outputs=())] * 2, "Duplicate stage"),
        ([StageSpec(name="source", outputs=("../source",))], "relative stage output"),
        (
            [
                StageSpec(name="source", outputs=("data",)),
                StageSpec(name="second", outputs=("data",)),
            ],
            "Duplicate stage output",
        ),
        (
            [
                StageSpec(name="source", outputs=("data",)),
                StageSpec(name="second", outputs=("data/file",)),
            ],
            "overlaps another output",
        ),
        (
            [
                StageSpec(name="source", outputs=(), needs=("build",)),
                StageSpec(name="build", outputs=(), needs=("source",)),
            ],
            "Cycle in flow stages",
        ),
        (
            [
                StageSpec(name="check", outputs=(), purpose="development"),
                StageSpec(name="source", outputs=(), needs=("check",)),
            ],
            "depends on development",
        ),
        ([StageSpec(name="build", outputs=(), needs=("missing",))], "Unknown stage 'missing'"),
        (
            [
                StageSpec(name="rtl", outputs=()),
                StageSpec(name="build", outputs=(), needs=("rtl", "rtl")),
            ],
            "Duplicate dependencies",
        ),
        ([StageSpec(name="rtl", outputs=("rtl//top.v",))], "relative stage output"),
    ),
)
def test_invalid_route_rejected(stages: list[StageSpec], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Route(stages)


def test_ready_branches_can_stop_independently() -> None:
    route = Route(
        [
            StageSpec(name="source", outputs=("source",)),
            StageSpec(name="build", outputs=("build",), needs=("source",)),
            StageSpec(name="data", outputs=("data",)),
            StageSpec(name="check", outputs=("report",), purpose="development"),
        ]
    )
    assert route._targets(route._ready(lambda stage: stage.name != "build")) == ("source", "data")
    assert route._targets(route._ready(lambda _: True, candidates={"source", "build"})) == (
        "build",
    )
