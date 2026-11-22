"""Capture the Python environment and resolve pinned tools for a flow."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from shutil import which

from pydantic import BaseModel, ConfigDict, Field

from ..api.records import JSON
from .artifacts import file_digest


class ImagePin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    image: str = Field(pattern=r"^[^\s@]+@sha256:[a-f0-9]{64}$")


class CommandPin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: str = Field(min_length=1)
    command: str | None = Field(default=None, min_length=1)


ToolPins = dict[str, ImagePin | CommandPin]


def pinned_image(pins: ToolPins, name: str, requested: str | None = None) -> str:
    pin = pins.get(name)
    if not isinstance(pin, ImagePin):
        raise ValueError(f"Declare tools.{name}.image with a sha256 digest in manifest.yml")
    if requested is not None and requested != pin.image:
        raise ValueError(f"The requested image differs from tools.{name}.image in manifest.yml")
    return pin.image


@dataclass(frozen=True)
class CheckedCommand:
    executable: str
    version: str

    @classmethod
    def resolve(cls, pins: ToolPins, name: str) -> CheckedCommand:
        pin = pins.get(name)
        if not isinstance(pin, CommandPin):
            raise ValueError(f"Declare tools.{name}.version in manifest.yml")
        command = pin.command or name
        executable = which(command)
        if executable is None:
            raise FileNotFoundError(f"Tool {name!r} needs {command!r} {pin.version} on PATH")
        output = subprocess.check_output(  # noqa: S603 - run the pinned tool by its resolved path.
            [executable, "--version"], stderr=subprocess.STDOUT, text=True
        ).strip()
        expected = rf"(?<![\w.+-]){re.escape(pin.version)}(?![\w.+-])"
        if not re.search(expected, output):
            actual = output.splitlines()[0] if output else "no version output"
            raise ValueError(f"{name} must be {pin.version!r}. {executable} reports {actual!r}")
        return cls(executable, pin.version)


def python_environment(
    packages: tuple[str, ...],
    *,
    source_roots: tuple[Path, ...],
    suffixes: tuple[str, ...] = (".py",),
) -> JSON:
    """Describe installed versions, the environment receipt and implementation files."""
    versions = {name: version(name) for name in packages}
    versions["python"] = sys.version
    receipt = Path(sys.prefix) / ".coreref-requirements.txt"
    sources = {
        root.name: {
            path.relative_to(root).as_posix(): file_digest(path)
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.suffix in suffixes
        }
        for root in source_roots
    }
    return {
        "versions": versions,
        "requirements": file_digest(receipt) if receipt.is_file() else None,
        "sources": sources,
    }
