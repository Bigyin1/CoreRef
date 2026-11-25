"""Generate the demonstration SRAM model from a selected geometry."""

from collections.abc import Mapping
from pathlib import Path
from typing import cast

from .rtl import write_source_list


def generate(work: Path, _log: Path, parameters: Mapping[str, object]) -> None:
    """Render a separate RTL definition for this depth and width."""
    depth, width = cast(int, parameters["depth"]), cast(int, parameters["width"])
    model = (work / "template").read_text(encoding="utf-8")
    model = model.replace("@DEPTH@", str(depth)).replace("@WIDTH@", str(width))
    model = model.replace("@ADDR@", str((depth - 1).bit_length()))
    directory = work / "hdl"
    directory.mkdir()
    source = directory / f"sram_{depth}x{width}.v"
    source.write_text(model, encoding="utf-8")
    write_source_list(directory, [source])
