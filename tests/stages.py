"""Importable Python callbacks for the end-to-end Nix tests."""

from collections.abc import Mapping
from pathlib import Path
from subprocess import check_output

from coreref.api.packages import JSON
from coreref.api.projects import Manifest


def prepare(work: Path, _log: Path, _configuration: Mapping[str, JSON]) -> None:
    (work / "small.txt").write_bytes((work / "input.txt").read_bytes())
    (work / "other.txt").write_text("separate result", encoding="utf-8")


def upper(work: Path, _log: Path, _configuration: Mapping[str, JSON]) -> None:
    assert not (work / "other.txt").exists()
    (work / "upper.txt").write_bytes((work / "small.txt").read_bytes().upper())


def host(work: Path, log: Path, _configuration: Mapping[str, JSON]) -> None:
    manifest = Manifest.load(work / "manifest.yml")
    command, _ = manifest.command("fixture-synth", version_args=("-version",))
    value = check_output([command, "run"], text=True)  # noqa: S603 - resolved tool under test.
    log.write_text("ran on host", encoding="utf-8")
    (work / "host.txt").write_text(value, encoding="utf-8")


def two_outputs(work: Path, log: Path, _configuration: Mapping[str, JSON]) -> None:
    value = (work / "input.txt").read_text(encoding="utf-8")
    log.write_text("ran", encoding="utf-8")
    (work / "small.txt").write_text(value, encoding="utf-8")
    if value != "missing":
        (work / "stage.log").write_text(value, encoding="utf-8")
        (work / "results.json").write_text("{}", encoding="utf-8")


def two_results(work: Path, _log: Path, _configuration: Mapping[str, JSON]) -> None:
    value = (work / "small.txt").read_text(encoding="utf-8")
    (work / "result.txt").write_text(value.upper(), encoding="utf-8")
    (work / "stage-copy.txt").write_text(value, encoding="utf-8")
