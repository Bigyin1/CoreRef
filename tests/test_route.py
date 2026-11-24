"""Check the resolved graph of a flow's declared stages."""

import pytest

from coreref.api.build.route import Route, StageSpec


def test_route_closure_visits_shared_stage_once() -> None:
    route = Route(
        [
            StageSpec(name="rtl", outputs=("rtl.v",)),
            StageSpec(name="dft", needs=("rtl",), outputs=("dft_output.v",)),
            StageSpec(name="synth", needs=("rtl", "dft"), outputs=("netlist.v",)),
        ]
    )
    assert tuple(stage.name for stage in route._closure(("synth",))) == ("rtl", "dft", "synth")


def test_publication_includes_transitive_checks() -> None:
    route = Route(
        [
            StageSpec(name="synth", outputs=("netlist.v",), checks=("compare",)),
            StageSpec(name="compare", outputs=(), needs=("synth",), checks=("report",)),
            StageSpec(name="report", outputs=("report.txt",), needs=("compare",)),
            StageSpec(name="lint", outputs=(), purpose="development"),
        ]
    )
    assert tuple(stage.name for stage in route._closure(("synth",))) == ("synth",)
    assert tuple(stage.name for stage in route._closure(("synth",), publishing=True)) == (
        "synth",
        "compare",
        "report",
    )
    with pytest.raises(ValueError, match="Expected a release stage"):
        route._closure(("lint",), publishing=True)


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
    assert route._targets(route._ready(lambda _: True, cap="build")) == ("build",)


def test_route_validates_handler_interface() -> None:
    route = Route(
        [
            StageSpec(name="prepare", outputs=("data",)),
            StageSpec(name="report", needs=("prepare",), outputs=("report.txt",)),
        ]
    )
    contracts = {"prepare": ("data",), "report": ("report.txt",)}
    route.validate_handlers(contracts, inputs={"report": ("data",)})
    with pytest.raises(ValueError, match="Unknown stage handler"):
        route.validate_handlers({"prepare": ("data",)}, inputs={})
    with pytest.raises(ValueError, match="must declare outputs"):
        route.validate_handlers({**contracts, "report": ("other",)}, inputs={})
    with pytest.raises(ValueError, match=r"needs 'report' for report\.txt"):
        route.validate_handlers(contracts, inputs={"prepare": ("report.txt",)})
