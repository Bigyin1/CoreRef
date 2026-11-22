"""Validate declarative stages and local imports in project routes."""

from pathlib import Path

import pytest

from coreref.api.build.route import Route


def test_imports_share_named_stages_in_a_diamond(tmp_path: Path) -> None:
    (tmp_path / "rtl.yml").write_text("stages: [{name: rtl, outputs: [rtl.v]}]")
    (tmp_path / "dft.yml").write_text(
        "imports: [rtl.yml]\nstages: [{name: dft, needs: [rtl], outputs: [dft_output.v]}]"
    )
    (tmp_path / "be.yml").write_text(
        "imports: [rtl.yml]\nstages: [{name: synth, needs: [rtl, dft], outputs: [netlist.v]}]"
    )
    (tmp_path / "flow.yml").write_text("imports: [be.yml, dft.yml]\n")
    route = Route.load(tmp_path)
    assert tuple(stage.name for stage in route.closure(("synth",))) == ("rtl", "dft", "synth")


@pytest.mark.parametrize(
    ("document", "message"),
    (
        (
            "stages: [{name: source, outputs: [source]}, {name: source, outputs: [source]}]",
            "Duplicate stage",
        ),
        ("stages: [{name: source, outputs: [../source]}]", "relative stage output"),
        (
            "stages: [{name: source, outputs: [data]}, {name: second, outputs: [data]}]",
            "Duplicate stage output",
        ),
        (
            "stages: [{name: source, outputs: [data]}, {name: second, outputs: [data/file]}]",
            "overlaps another output",
        ),
        (
            "stages: [{name: source, outputs: [source], needs: [build]}, "
            "{name: build, outputs: [build], needs: [source]}]",
            "Cycle in flow stages",
        ),
        (
            "stages: [{name: check, outputs: [report], purpose: development}, "
            "{name: source, outputs: [source], needs: [check]}]",
            "depends on development",
        ),
        ("imports: [flow.yml]", "Cycle in flow imports"),
        ("imports: [../elsewhere.yml]", "within the project"),
    ),
)
def test_invalid_route_rejected(tmp_path: Path, document: str, message: str) -> None:
    (tmp_path / "flow.yml").write_text(document)
    with pytest.raises(ValueError, match=message):
        Route.load(tmp_path)


def test_ready_branches_can_stop_independently(tmp_path: Path) -> None:
    (tmp_path / "flow.yml").write_text(
        "stages:\n"
        "  - {name: source, outputs: [source]}\n"
        "  - {name: build, outputs: [build], needs: [source]}\n"
        "  - {name: data, outputs: [data]}\n"
        "  - {name: check, outputs: [report], purpose: development}\n"
    )
    route = Route.load(tmp_path)
    assert route.targets(route.ready(lambda name: name != "build")) == ("source", "data")
    assert route.targets(route.ready(lambda _: True, cap="build")) == ("build",)


def test_route_validates_handler_interface(tmp_path: Path) -> None:
    (tmp_path / "flow.yml").write_text(
        "stages: [{name: prepare, outputs: [data]}, "
        "{name: report, needs: [prepare], outputs: [report.txt]}]"
    )
    route = Route.load(tmp_path)
    contracts = {"prepare": ("data",), "report": ("report.txt",)}
    route.validate_handlers(contracts, inputs={"report": ("data",)})
    assert route.release_document() == [
        {"name": "prepare", "needs": (), "outputs": ("data",)},
        {"name": "report", "needs": ("prepare",), "outputs": ("report.txt",)},
    ]
    with pytest.raises(ValueError, match="Unknown stage handler"):
        route.validate_handlers({"prepare": ("data",)}, inputs={})
    with pytest.raises(ValueError, match="must declare outputs"):
        route.validate_handlers({**contracts, "report": ("other",)}, inputs={})
    with pytest.raises(ValueError, match=r"needs 'report' for report\.txt"):
        route.validate_handlers(contracts, inputs={"prepare": ("report.txt",)})
