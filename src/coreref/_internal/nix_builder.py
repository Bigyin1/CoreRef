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
from tempfile import TemporaryDirectory


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


def _install(work: Path, filenames: Mapping[Path, str | None], stage: str) -> None:
    for directory, filename in filenames.items():
        directory.mkdir(parents=True, exist_ok=True)
        if filename is None:
            continue
        path = work / filename
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Stage {stage} did not produce {filename}")
        target = directory / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, target)


def run(plan_path: Path, log: Path, host_output: Path | None = None) -> None:
    """Stage the inputs, call the flow function, and materialize its outputs."""
    plan = json.loads(plan_path.read_bytes())
    _load_modules(plan["modules"])
    module_name, function = plan["transform"].split(":")
    transform = getattr(import_module(module_name), function)
    with TemporaryDirectory(prefix="coreref-", dir=log.parent) as temporary:
        work = Path(temporary)
        for filename, source in plan["inputs"].items():
            target = work / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source, target)
            target.chmod(stat.S_IMODE(target.stat().st_mode) | stat.S_IWUSR)
        transform(work, log, plan["configuration"])
        if host_output is not None:
            # A separate staging directory for each output retains the same contract as Nix.
            outputs = {host_output / key: name for key, name in plan["outputs"].items()}
        else:
            outputs = {Path(os.environ[key]): name for key, name in plan["outputs"].items()}
        _install(work, outputs, plan["name"])


def main() -> None:
    """Run either as a derivation builder or as a host worker."""
    if arguments := sys.argv[1:]:
        plan, log, output = map(Path, arguments)
        run(plan, log, output)
        return
    log = Path.cwd() / "stage.log"
    plan = Path(os.environ["stagePlanPath"])  # noqa: SIM112 - passAsFile appends Path.
    try:
        run(plan, log)
    except Exception:
        if log.is_file():
            print(log.read_text(errors="replace"), file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
