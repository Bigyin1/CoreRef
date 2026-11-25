"""Combine routed DEF with standard-cell and macro geometries."""

from collections.abc import Mapping
from pathlib import Path

import klayout.db as pya  # type: ignore[import-not-found]


def _read_def(work: Path, platform: Path, has_macro: bool) -> pya.Layout:
    technology = pya.Technology()
    technology.load(str(platform / "FreePDK45.lyt"))
    options = technology.load_layout_options
    options.lefdef_config.dbu = technology.dbu
    lefs = [
        str(platform / "lef" / "NangateOpenCellLibrary.tech.lef"),
        str(platform / "lef" / "NangateOpenCellLibrary.macro.mod.lef"),
    ]
    if has_macro:
        lefs.append(str(work / "macro.lef"))
    options.lefdef_config.lef_files = lefs
    layout = pya.Layout()
    layout.read(str(work / "final.def"), options)
    return layout


def stream(work: Path, _log: Path, parameters: Mapping[str, object]) -> None:
    """Write the final GDS and verify that each referenced cell has geometry."""
    platform = Path(str(parameters["platform"]))
    top_name, macro_name = str(parameters["top"]), str(parameters["macro"])
    layout = _read_def(work, platform, bool(macro_name))
    top = layout.cell(top_name)
    for cell in layout.each_cell():
        if cell != top and not cell.name.startswith("VIA_"):
            cell.clear()
    layout.read(str(platform / "gds" / "NangateOpenCellLibrary.gds"))
    if macro_name:
        layout.read(str(work / "macro.gds"))
    result = pya.Layout()
    result.dbu = layout.dbu
    result.create_cell(top_name).copy_tree(top)
    empty = [cell.name for cell in result.each_cell() if cell.is_empty()]
    if empty:
        raise RuntimeError(f"Cells without GDS geometry: {', '.join(empty)}")
    if macro_name and result.cell(macro_name) is None:
        raise RuntimeError(f"Missing macro {macro_name} in GDS")
    result.write(str(work / "final.gds"))
