"""Validate declarative stages and local imports in project routes."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from coreref.api.build.route import Route, StageSpec


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
    assert tuple(stage.name for stage in route._closure(("synth",))) == ("rtl", "dft", "synth")


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
        ("imports: [/other.yml]", "relative flow import"),
        ("stages: [", "Invalid flow YAML"),
        ("stages: [{name: build, outputs: [], needs: [missing]}]", "Unknown stage 'missing'"),
        (
            "stages: [{name: rtl, outputs: []}, {name: build, outputs: [], needs: [rtl, rtl]}]",
            "Duplicate dependencies",
        ),
        ("stages: [{name: rtl, outputs: [rtl//top.v]}]", "relative stage output"),
        ("imports: [../elsewhere.yml]", "within the project"),
        ("use: [missing]", "Unknown route module"),
        ("use: [dup, dup]", "Duplicate route module"),
        ("overrides: {missing: {add_needs: []}}", "Unknown stage in overrides"),
        ("unknown: true", "Extra inputs are not permitted"),
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
    assert route._targets(route._ready(lambda stage: stage.name != "build")) == ("source", "data")
    assert route._targets(route._ready(lambda _: True, cap="build")) == ("build",)


def test_route_validates_handler_interface(tmp_path: Path) -> None:
    (tmp_path / "flow.yml").write_text(
        "stages: [{name: prepare, outputs: [data]}, "
        "{name: report, needs: [prepare], outputs: [report.txt]}]"
    )
    route = Route.load(tmp_path)
    contracts = {"prepare": ("data",), "report": ("report.txt",)}
    route.validate_handlers(contracts, inputs={"report": ("data",)})
    assert route._release_document() == [
        {"name": "prepare", "needs": (), "outputs": ("data",)},
        {"name": "report", "needs": ("prepare",), "outputs": ("report.txt",)},
    ]
    with pytest.raises(ValueError, match="Unknown stage handler"):
        route.validate_handlers({"prepare": ("data",)}, inputs={})
    with pytest.raises(ValueError, match="must declare outputs"):
        route.validate_handlers({**contracts, "report": ("other",)}, inputs={})
    with pytest.raises(ValueError, match=r"needs 'report' for report\.txt"):
        route.validate_handlers(contracts, inputs={"prepare": ("report.txt",)})


def test_route_rejects_import_symlink_leaving_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "outside.yml"
    external.write_text("stages: [{name: rtl, outputs: [rtl.v]}]")
    (project / "linked.yml").symlink_to(external)
    (project / "flow.yml").write_text("imports: [linked.yml]")

    with pytest.raises(ValueError, match="within the project"):
        Route.load(project)


def test_nested_route_imports_resolve_relative_to_the_importing_file(tmp_path: Path) -> None:
    module = tmp_path / "module"
    module.mkdir()
    (tmp_path / "flow.yml").write_text("imports: [module/be.yml]")
    (tmp_path / "shared.yml").write_text("stages: [{name: rtl, outputs: [rtl.v]}]")
    (module / "be.yml").write_text(
        "imports: [../shared.yml]\nstages: [{name: be, needs: [rtl], outputs: [final.gds]}]"
    )

    route = Route.load(tmp_path)
    assert route.order == ("rtl", "be")
    assert route["be"].needs == ("rtl",)


def test_load_combines_imports_fragments_and_overrides_before_selection(tmp_path: Path) -> None:
    (tmp_path / "check.yml").write_text("stages: [{name: test, outputs: [report]}]")
    (tmp_path / "flow.yml").write_text(
        "imports: [check.yml]\n"
        "use: [compile]\n"
        "overrides: {assemble: {add_needs: [test]}}\n"
        "extra_checks: false\n"
    )
    fragments = {"compile": (StageSpec(name="assemble", outputs=("object",)),)}

    def select(stages: list[StageSpec], options: Mapping[str, object]) -> list[StageSpec]:
        assert options == {"extra_checks": False}
        assert [stage.name for stage in stages] == ["test", "assemble"]
        assert stages[1].needs == ("test",)
        return [stages[1].model_copy(update={"needs": ()})]

    route = Route.load(tmp_path, fragments=fragments, select=select)
    assert route.order == ("assemble",)
    assert fragments["compile"][0].needs == ()
