"""A portable uv environment for an installed producer entry point."""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def producer_files(project: Path) -> dict[str, Path]:
    """Return the files needed to recreate the producer's Python environment."""
    return {name: project / name for name in ("pyproject.toml", "uv.lock", ".python-version")}


@dataclass(frozen=True)
class ProducerRuntime:
    """Run a Python module with packages locked in a producer's project."""

    project: Path
    module: str | None = None

    def _arguments(self) -> tuple[str, ...]:
        if self.module is None:
            return ("-m", "coreref._internal.producer")
        if ":" in self.module:
            return ("-m", "coreref._internal.producer", self.module)
        return ("-m", self.module)

    @property
    def files(self) -> dict[str, Path]:
        return producer_files(self.project)

    def validate(self) -> None:
        for path in self.files.values():
            if not path.is_file():
                raise ValueError(f"Publishing rebuild recipes requires {path}")
        python = self.files[".python-version"].read_text(encoding="utf-8").strip()
        if re.fullmatch(r"(?:cpython@)?\d+\.\d+\.\d+", python) is None:
            raise ValueError("Producer .python-version must pin an exact CPython version")
        self._validate_lock()

    def _validate_lock(self) -> None:
        document = tomllib.loads(self.files["uv.lock"].read_text(encoding="utf-8"))
        for package in document.get("package", ()):
            source = package["source"]
            if source == {"virtual": "."}:
                continue
            if not source.get("registry", "").startswith(("https://", "http://")):
                raise ValueError(
                    f"Producer uv.lock must resolve {package['name']} from a Python registry"
                )
