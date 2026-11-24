"""Importable Python callbacks for the end-to-end Nix tests."""

from collections.abc import Mapping
from os import environ
from pathlib import Path

from coreref.api.packages import JSON


def prepare(work: Path, _log: Path, _configuration: Mapping[str, JSON]) -> None:
    (work / "small.txt").write_bytes((work / "input.txt").read_bytes())
    (work / "other.txt").write_text("separate result", encoding="utf-8")


def upper(work: Path, _log: Path, _configuration: Mapping[str, JSON]) -> None:
    assert not (work / "other.txt").exists()
    (work / "upper.txt").write_bytes((work / "small.txt").read_bytes().upper())


def host(work: Path, log: Path, _configuration: Mapping[str, JSON]) -> None:
    log.write_text("ran on host", encoding="utf-8")
    value = environ["COREREF_TEST_MODULE"]
    (work / "host.txt").write_text(value, encoding="utf-8")
