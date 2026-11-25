"""Combine routed DEF with standard-cell and macro geometries."""

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import klayout.db as pya  # type: ignore[import-not-found]


def _read_def(work: Path, platform: Path) -> pya.Layout:
    technology = pya.Technology()
    technology.load(str(platform / "FreePDK45.lyt"))
    options = technology.load_layout_options
    options.lefdef_config.dbu = technology.dbu
    lefs = [
        str(platform / "lef" / "NangateOpenCellLibrary.tech.lef"),
        str(platform / "lef" / "NangateOpenCellLibrary.macro.mod.lef"),
    ]
    lefs.extend(map(str, sorted((work / "macros").glob("*.lef"))))
    options.lefdef_config.lef_files = lefs
    layout = pya.Layout()
    layout.read(str(work / "final.def"), options)
    return layout


def stream(work: Path, _log: Path, parameters: Mapping[str, object]) -> None:
    """Write the final GDS and verify that each referenced cell has geometry."""
    platform = Path(str(parameters["platform"]))
    top_name = str(parameters["top"])
    macro_cells = cast(list[str], parameters["macroCells"])
    layout = _read_def(work, platform)
    top = layout.cell(top_name)
    for cell in layout.each_cell():
        if cell != top and not cell.name.startswith("VIA_"):
            cell.clear()
    layout.read(str(platform / "gds" / "NangateOpenCellLibrary.gds"))
    for gds in sorted((work / "macros").glob("*.gds")):
        layout.read(str(gds))
    result = pya.Layout()
    result.dbu = layout.dbu
    result.create_cell(top_name).copy_tree(top)
    empty = [cell.name for cell in result.each_cell() if cell.is_empty()]
    if empty:
        raise RuntimeError(f"Cells without GDS geometry: {', '.join(empty)}")
    missing = [name for name in macro_cells if result.cell(name) is None]
    if missing:
        raise RuntimeError(f"Missing macros in GDS: {', '.join(missing)}")
    result.write(str(work / "final.gds"))
