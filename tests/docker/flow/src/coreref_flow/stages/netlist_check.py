"""Check the expected top module in the synthesized Verilog."""

import json
import re
from collections.abc import Mapping
from pathlib import Path


def check_synthesis(work: Path, _log: Path, parameters: Mapping[str, object]) -> None:
    """Write a report only if the configured top module is present."""
    top = str(parameters["top"])
    netlist = (work / "netlist.v").read_text(encoding="utf-8")
    if re.search(rf"(?m)^\s*module\s+{re.escape(top)}\b", netlist) is None:
        raise ValueError(f"Synthesized netlist does not declare module {top}")
    (work / "netlist-check.json").write_text(json.dumps({"top": top}) + "\n", encoding="utf-8")
