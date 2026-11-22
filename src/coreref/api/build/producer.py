"""Execute a recipe's prepared stages with a handler from the installed Flow."""

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from typing import TypedDict, cast

from ..packages.records import JSON


@dataclass(frozen=True)
class ProducerStage:
    """The stage's inputs are already present in its working directory."""

    name: str
    directory: Path
    log: Path
    configuration: Mapping[str, JSON]
    pipeline_configuration: Mapping[str, JSON]


_Handler = Callable[[ProducerStage], None]


class _Stage(TypedDict):
    name: str
    inputs: list[str]
    outputs: list[str]
    configuration: dict[str, JSON]


class _Pipeline(TypedDict):
    configuration: dict[str, JSON]
    stages: list[_Stage]


def check_outputs(directory: Path, outputs: Sequence[str], log: Path) -> None:
    """Check that each declared output is a regular file."""
    for name in outputs:
        output = directory / name
        if output.is_symlink() or not output.is_file():
            raise RuntimeError(f"{log.stem} produced no {name}. See {log}.")


def _run_stage(
    stage: _Stage, directory: Path, configuration: Mapping[str, JSON], handler: _Handler
) -> None:
    name = stage["name"]
    log = directory / f"{name}.log"
    with TemporaryDirectory(prefix=f"{name}-", dir=directory) as temporary:
        work = Path(temporary)
        for filename in stage["inputs"]:
            target = work / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            copyfile(directory / filename, target)
        handler(ProducerStage(name, work, log, stage.get("configuration", {}), configuration))
        check_outputs(work, stage["outputs"], log)
        for filename in stage["outputs"]:
            target = directory / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            (work / filename).replace(target)


def _load_handler(entry: str) -> _Handler:
    module, _, name = entry.partition(":")
    if not module or not name:
        raise argparse.ArgumentTypeError("Expected a producer handler in module:function form")
    handler = getattr(import_module(module), name)
    if not callable(handler):
        raise argparse.ArgumentTypeError(f"Producer handler {entry!r} must be callable")
    return cast(_Handler, handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handler", type=_load_handler)
    parser.add_argument("layout", type=Path)
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    pipeline = cast(_Pipeline, json.loads(arguments.layout.read_bytes()))
    for stage in pipeline["stages"]:
        _run_stage(stage, arguments.directory, pipeline["configuration"], arguments.handler)


if __name__ == "__main__":
    main()
