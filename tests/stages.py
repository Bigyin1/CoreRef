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
