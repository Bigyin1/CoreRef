"""Run a selected local route and reuse files across separate invocations."""

from __future__ import annotations

from pathlib import Path

import pytest

from coreref.tools.pipeline import BuildStage, Pipeline


def test_pipeline_tracks_dependencies_files_and_configuration(tmp_path: Path) -> None:
    source = tmp_path / "rtl"
    source.write_text("initial")
    calls: list[str] = []

    def write(name: str, dependency: str) -> None:
        calls.append(name)
        (tmp_path / name).write_text((tmp_path / dependency).read_text())

    def pipeline(options: str) -> Pipeline:
        synth = BuildStage(
            name="synthesis",
            action=lambda: write("netlist", "rtl"),
            inputs=("rtl",),
            outputs=("netlist",),
        )
        layout = BuildStage(
            name="layout",
            action=lambda: write("gds", "netlist"),
            inputs=("netlist",),
            outputs=("gds",),
            configuration={"options": options},
        )
        lint = BuildStage(
            name="lint", action=lambda: calls.append("lint"), inputs=("rtl",), outputs=()
        )
        return Pipeline(tmp_path, (layout, synth, lint))

    pipeline("fast").run("layout")
    assert calls == ["netlist", "gds"]
    pipeline("fast").run("layout")
    assert calls == ["netlist", "gds"]
    pipeline("slow").run("layout")
    assert calls == ["netlist", "gds", "gds"]
    source.write_text("changed")
    pipeline("slow").run("layout")
    assert calls[-2:] == ["netlist", "gds"]
    assert (tmp_path / "gds").read_text() == "changed"
    (tmp_path / "netlist").unlink()
    pipeline("slow").run("layout")
    assert calls == ["netlist", "gds", "gds", "netlist", "gds", "netlist"]


def test_pipeline_rejects_unknown_targets_and_missing_outputs(tmp_path: Path) -> None:
    stage = BuildStage(name="report", action=lambda: None, inputs=(), outputs=("report.txt",))
    pipeline = Pipeline(tmp_path, (stage,))
    with pytest.raises(ValueError, match="Unknown stage"):
        pipeline.run("absent")
    with pytest.raises(RuntimeError, match="Build stage failed"):
        pipeline.run("report")
