"""Resolve tools selected by the project manifest."""

from __future__ import annotations

import re
import subprocess
from shutil import which

from pydantic import BaseModel, ConfigDict, Field


class ImagePin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    image: str = Field(pattern=r"^[^\s@]+@sha256:[a-f0-9]{64}$")


class CommandPin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: str = Field(min_length=1)
    command: str | None = Field(default=None, min_length=1)


ToolPins = dict[str, ImagePin | CommandPin]


def checked_command(pins: ToolPins, name: str, *, version_args: tuple[str, ...]) -> tuple[str, str]:
    pin = pins.get(name)
    if not isinstance(pin, CommandPin):
        raise ValueError(f"Declare tools.{name}.version in manifest.yml")
    command = pin.command or name
    executable = which(command)
    if executable is None:
        raise FileNotFoundError(f"Tool {name!r} needs {command!r} {pin.version} on PATH")
    output = subprocess.check_output(  # noqa: S603 - run the pinned tool by its resolved path.
        [executable, *version_args], stderr=subprocess.STDOUT, text=True
    ).strip()
    expected = rf"(?<![\w.+-]){re.escape(pin.version)}(?![\w.+-])"
    if not re.search(expected, output):
        actual = output.splitlines()[0] if output else "no version output"
        raise ValueError(f"{name} must be {pin.version!r}. {executable} reports {actual!r}")
    return executable, pin.version
