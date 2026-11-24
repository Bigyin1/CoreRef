"""Execute a recipe's prepared stages with transforms from the installed flow."""

import argparse
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from importlib import import_module
from inspect import getsourcefile
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from typing import TypedDict, cast

from typing_extensions import NotRequired

from ..api.packages.records import JSON
from .hashing import file_digest

_Transform = Callable[[Path, Path, Mapping[str, JSON]], None]


class _Stage(TypedDict):
    name: str
    inputs: list[str]
    outputs: list[str]
    transform: str
    configuration: NotRequired[dict[str, JSON]]


class _Pipeline(TypedDict):
    stages: list[_Stage]


def check_outputs(directory: Path, outputs: Sequence[str], log: Path) -> None:
    """Check that each declared output is a regular file."""
    for name in outputs:
        output = directory / name
        if output.is_symlink() or not output.is_file():
            raise RuntimeError(f"{log.stem} produced no {name}. See {log}.")


@contextmanager
def workspace(
    directory: Path,
    name: str,
    inputs: Mapping[str, Path],
    outputs: Sequence[str],
) -> Iterator[tuple[Path, Path]]:
    """Isolate stage inputs and commit the outputs only after complete validation."""
    log = directory / f"{name}.log"
    with TemporaryDirectory(prefix=f"{name}-", dir=directory) as temporary:
        work = Path(temporary)
        for filename, source in inputs.items():
            target = work / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            copyfile(source, target)
        yield work, log
        check_outputs(work, outputs, log)
        for filename in outputs:
            target = directory / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            (work / filename).replace(target)


def _run_stage(stage: _Stage, directory: Path) -> None:
    inputs = {name: directory / name for name in stage["inputs"]}
    transform = _load_transform(stage["transform"])
    with workspace(directory, stage["name"], inputs, stage["outputs"]) as (work, log):
        transform(work, log, stage.get("configuration", {}))


def transform_reference(transform: _Transform) -> str:
    name = getattr(transform, "__qualname__", "")
    module = getattr(transform, "__module__", "")
    if not module or module == "__main__" or not name.isidentifier():
        raise ValueError("A rebuildable transform must be a module-level function")
    return f"{module}:{name}"


def transform_identity(transform: _Transform) -> dict[str, JSON]:
    code = getattr(transform, "__code__", None)
    source = getsourcefile(code) if code is not None else None
    path = Path(source) if source is not None else None
    return {
        "module": getattr(transform, "__module__", None),
        "function": getattr(transform, "__qualname__", type(transform).__qualname__),
        "implementation": file_digest(path) if path is not None and path.is_file() else None,
    }


def _load_transform(entry: str) -> _Transform:
    module, _, name = entry.partition(":")
    if not module or not name:
        raise ValueError("Expected a producer transform in module:function form")
    transform = getattr(import_module(module), name)
    if not callable(transform):
        raise ValueError(f"Producer transform {entry!r} must be callable")
    return cast(_Transform, transform)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("layout", type=Path)
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    pipeline = cast(_Pipeline, json.loads(arguments.layout.read_bytes()))
    for stage in pipeline["stages"]:
        _run_stage(stage, arguments.directory)


if __name__ == "__main__":
    main()
