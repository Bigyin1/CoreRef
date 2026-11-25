"""Keep per-project input substitutions in local workspace state."""

from __future__ import annotations

import json
from fcntl import LOCK_EX, flock
from pathlib import Path
from urllib.parse import quote

_STATE = Path(".coreref/workspace.json")


def read(directory: Path) -> dict[str, Path]:
    """Read the current checkout for each overridden input."""
    try:
        document = json.loads((directory / _STATE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {name: Path(path) for name, path in document.items()}


def update(directory: Path, name: str, checkout: Path | None) -> bool:
    """Update one checkout atomically under a lock shared by workspace writers."""
    state = directory / _STATE
    if checkout is None and not state.exists():
        return False
    state.parent.mkdir(exist_ok=True)
    with state.with_suffix(".lock").open("a", encoding="utf-8") as lock:
        flock(lock, LOCK_EX)
        overrides = read(directory)
        if overrides.get(name) == checkout:
            return False
        if checkout is None:
            del overrides[name]
        else:
            overrides[name] = checkout
        staged = state.with_suffix(".tmp")
        document = {input_name: str(path) for input_name, path in overrides.items()}
        try:
            staged.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            staged.replace(state)
        finally:
            staged.unlink(missing_ok=True)
    return True


def arguments(checkouts: dict[str, Path]) -> list[str]:
    """Translate workspace entries to standard Nix override flags."""
    flags: list[str] = []
    for name, directory in checkouts.items():
        ref = f"path:{quote(str(directory), safe='/')}"
        if (directory / ".git").exists():
            ref = f"git+{directory.as_uri()}"
        flags.extend(("--override-input", name, ref))
    return flags
