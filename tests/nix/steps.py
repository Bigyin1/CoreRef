"""Test transforms for the native stage graph."""

import subprocess
from collections.abc import Mapping
from pathlib import Path


def produce(work: Path, _log: Path, _params: Mapping[str, object]) -> None:
    """Produce two independently selectable outputs."""
    (work / "data.txt").write_text("data", encoding="utf-8")
    summary = subprocess.check_output(["stage-tool"], text=True).strip()  # noqa: S607
    (work / "summary.txt").write_text(summary, encoding="utf-8")


def consume(work: Path, _log: Path, params: Mapping[str, object]) -> None:
    """Join a single named output with a project parameter."""
    summary = (work / "summary.txt").read_text(encoding="utf-8")
    label = subprocess.check_output(["stage-tool"], text=True).strip()  # noqa: S607
    (work / "result.txt").write_text(f"{summary} {label} {params['label']}", encoding="utf-8")


def consume_directories(work: Path, _log: Path, _params: Mapping[str, object]) -> None:
    """Read both the default and explicitly selected output directories."""
    data = (work / "data" / "data.txt").read_text(encoding="utf-8")
    summary = (work / "summary" / "summary.txt").read_text(encoding="utf-8")
    ordered = sorted((work / "ordered").glob("*/*"))
    assert [file.name for directory in ordered for file in directory.iterdir()] == [
        "data.txt",
        "summary.txt",
    ]
    (work / "result.txt").write_text(f"{data} {summary}", encoding="utf-8")


def licensed(work: Path, _log: Path, _params: Mapping[str, object]) -> None:
    """Run the sample tool with a license endpoint provided by the worker."""
    with (work / "result.txt").open("w", encoding="utf-8") as stream:
        subprocess.run(["example-tool"], stdout=stream, check=True)  # noqa: S607 - test module
