"""Check the mixed RTL, LEF and Liberty inputs of the physical lint example."""

import json
from collections.abc import Mapping
from pathlib import Path

from .rtl import read_source_list


def check_inputs(work: Path, _log: Path, parameters: Mapping[str, object]) -> None:
    """Record the views provided to a mixed-representation linter."""
    rtl = read_source_list(work / "rtl")
    abstracts = sorted((work / "macros").glob("*/*"))
    for path in (*rtl, *abstracts):
        if path.stat().st_size == 0:
            raise ValueError(f"Empty lint input: {path.relative_to(work)}")
    document = {
        "check": "physical-lint-inputs",
        "top": parameters["top"],
        "rtl": [str(path.relative_to(work)) for path in rtl],
        "macroInputs": [str(path.relative_to(work)) for path in abstracts],
    }
    (work / "inputs.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
