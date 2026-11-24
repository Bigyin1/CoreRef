"""Resolve tools selected by the project manifest."""

from __future__ import annotations

import re
import subprocess
from shutil import which
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

_TOOL_REFERENCE = re.compile(r"([^\s/]+)/([^\s/]+)")


class ImagePin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    image: str = Field(pattern=r"^[^\s@]+@sha256:[a-f0-9]{64}$")


class CommandPin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: str = Field(min_length=1)
    command: str | None = Field(default=None, min_length=1)


def _tool_entries(value: object) -> dict[object, object]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or (pin := _TOOL_REFERENCE.fullmatch(value)) is None:
        raise ValueError(f"Expected tool name/version or a named tool mapping, got {value!r}")
    name, version = pin.groups()
    return {name: {"version": version}}


def _parse_tool_list(value: object) -> object:
    if not isinstance(value, list):
        return value
    pins: dict[object, object] = {}
    for item in value:
        for name, pin in _tool_entries(item).items():
            if name in pins:
                raise ValueError(f"Duplicate tool {name!r}")
            pins[name] = pin
    return pins


_NamedToolPins = dict[str, ImagePin | CommandPin]
ToolPins = Annotated[
    _NamedToolPins,
    BeforeValidator(
        _parse_tool_list,
        json_schema_input_type=_NamedToolPins | list[str | _NamedToolPins],
    ),
]


def checked_command(pins: ToolPins, name: str, *, version_args: tuple[str, ...]) -> tuple[str, str]:
    pin = pins.get(name)
    if not isinstance(pin, CommandPin):
        raise ValueError(f"Declare a version for tool {name!r} in manifest.yml")
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
