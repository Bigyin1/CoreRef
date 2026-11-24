"""Invoke one importable Python stage with its declared files and configuration."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from collections.abc import Mapping
from importlib import import_module, util
from pathlib import Path
from typing import Any


def _load_modules(modules: dict[str, str]) -> None:
    # Relative imports and resources use the tracked source snapshot.
    for name, path in modules.items():
        package = Path(path)
        init = package / "__init__.py" if package.is_dir() else package
        spec = util.spec_from_file_location(name, init)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot import {name} from {init}")
        module = util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)


def _install(work: Path, filenames: Mapping[Path, str | None]) -> None:
    for directory, filename in filenames.items():
        directory.mkdir(parents=True, exist_ok=True)
        if filename is None:
            continue
        path = work / filename
        target = directory / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, target)


def run(plan: Mapping[str, Any], work: Path, log: Path) -> None:
    """Stage writable inputs, run the transform, and check each declared output."""
    _load_modules(plan["modules"])
    module_name, function = plan["transform"].split(":")
    transform = getattr(import_module(module_name), function)
    work.mkdir()
    for filename, source in plan["inputs"].items():
        target = work / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, target)
        target.chmod(stat.S_IMODE(target.stat().st_mode) | stat.S_IWUSR)
    transform(work, log, plan["configuration"])
    for filename in plan["outputs"].values():
        if filename is None:
            continue
        path = work / filename
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Stage {plan['name']} did not produce {filename}")


def main() -> None:
    """Run either as a derivation builder or as a host worker."""
    work = Path.cwd() / "work"
    if len(sys.argv) > 1:
        run(json.load(sys.stdin), work, Path(sys.argv[1]))
        return
    log = Path.cwd() / "stage.log"
    plan = Path(os.environ["stagePlanPath"])  # noqa: SIM112 - passAsFile appends Path.
    try:
        document = json.loads(plan.read_bytes())
        run(document, work, log)
        outputs = {Path(os.environ[key]): name for key, name in document["outputs"].items()}
        _install(work, outputs)
    finally:
        if log.is_file():
            with log.open(encoding="utf-8", errors="replace") as stream:
                shutil.copyfileobj(stream, sys.stderr)


if __name__ == "__main__":
    main()
